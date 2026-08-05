import { dlopen, FFIType, read, type FFIFunction, type Pointer } from "bun:ffi";

export type WriterNativeLibrary = {
  openat(
    directory: number,
    path: Uint8Array,
    flags: number,
    mode: number,
  ): number;
  linkat(
    sourceDirectory: number,
    source: Uint8Array,
    targetDirectory: number,
    target: Uint8Array,
    flags: number,
  ): number;
  unlinkat(directory: number, path: Uint8Array, flags: number): number;
  flock(descriptor: number, operation: number): number;
  errno(): number;
  close(): void;
};

function signature<
  const Args extends readonly FFIType[],
  const Result extends FFIType,
>(
  args: Args,
  returns: Result,
): { readonly args: Args; readonly returns: Result } {
  return { args, returns };
}

const COMMON_DEFINITIONS = {
  openat: signature(
    [FFIType.int, FFIType.cstring, FFIType.int, FFIType.int],
    FFIType.int,
  ),
  linkat: signature(
    [FFIType.int, FFIType.cstring, FFIType.int, FFIType.cstring, FFIType.int],
    FFIType.int,
  ),
  unlinkat: signature([FFIType.int, FFIType.cstring, FFIType.int], FFIType.int),
  flock: signature([FFIType.int, FFIType.int], FFIType.int),
} satisfies Record<string, FFIFunction>;

function requiredPointer(pointer: Pointer | null, symbol: string): Pointer {
  if (pointer === null)
    throw new Error(`[writer] failure_kind=errno-pointer symbol=${symbol}`);
  return pointer;
}

function darwinLibrary(): WriterNativeLibrary {
  const loaded = dlopen("/usr/lib/libSystem.B.dylib", {
    ...COMMON_DEFINITIONS,
    __error: signature([], FFIType.ptr),
  });
  return {
    openat: loaded.symbols.openat,
    linkat: loaded.symbols.linkat,
    unlinkat: loaded.symbols.unlinkat,
    flock: loaded.symbols.flock,
    errno: () => read.i32(requiredPointer(loaded.symbols.__error(), "__error")),
    close: () => loaded.close(),
  };
}

function linuxLibrary(): WriterNativeLibrary {
  const loaded = dlopen("libc.so.6", {
    ...COMMON_DEFINITIONS,
    __errno_location: signature([], FFIType.ptr),
  });
  return {
    openat: loaded.symbols.openat,
    linkat: loaded.symbols.linkat,
    unlinkat: loaded.symbols.unlinkat,
    flock: loaded.symbols.flock,
    errno: () =>
      read.i32(
        requiredPointer(loaded.symbols.__errno_location(), "__errno_location"),
      ),
    close: () => loaded.close(),
  };
}

export function loadWriterNativeLibrary(): WriterNativeLibrary {
  return process.platform === "darwin" ? darwinLibrary() : linuxLibrary();
}
