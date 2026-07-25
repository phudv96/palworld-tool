import ctypes
import os
import zlib

MAGIC_BYTES_PLZ = b"PlZ"
MAGIC_BYTES_PLM = b"PlM"

def decompress_oodle(compressed_data: bytes, uncompressed_size: int) -> bytes:
    # Tìm file DLL ở thư mục hiện tại hoặc thư mục cha
    dll_path = os.path.abspath("oo2core_9_win64.dll")
    if not os.path.exists(dll_path):
        dll_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "oo2core_9_win64.dll"))
    
    if not os.path.exists(dll_path):
        raise FileNotFoundError("Khong tim thấy file oo2core_9_win64.dll!")

    oodle = ctypes.cdll.LoadLibrary(dll_path)
    out_buf = ctypes.create_string_buffer(uncompressed_size)
    result = oodle.OodleLZ_Decompress(
        compressed_data, len(compressed_data),
        out_buf, uncompressed_size,
        0, 0, 0, None, None, None, None, None, None, 0
    )
    if result <= 0:
        raise Exception(f"Giai ma Oodle that bai voi ma loi: {result}")
    return out_buf.raw

def decompress_sav_to_gvas(data: bytes) -> tuple[bytes, int]:
    uncompressed_len = int.from_bytes(data[0:4], byteorder="little")
    compressed_len = int.from_bytes(data[4:8], byteorder="little")
    magic_bytes = data[8:11]
    save_type = data[11]

    if save_type == 0:
        return data[12:], save_type

    if magic_bytes == MAGIC_BYTES_PLZ:
        return zlib.decompress(data[12:]), save_type
    elif magic_bytes == MAGIC_BYTES_PLM:
        return decompress_oodle(data[12:], uncompressed_len), save_type
    else:
        raise Exception(f"Khong ho tro dinh dang save: {magic_bytes!r}")

def compress_gvas_to_sav(data: bytes, save_type: int) -> bytes:
    if save_type == 0:
        return data
    uncompressed_len = len(data)
    compressed_data = zlib.compress(data)
    compressed_len = len(compressed_data)
    
    return (
        uncompressed_len.to_bytes(4, byteorder="little")
        + compressed_len.to_bytes(4, byteorder="little")
        + MAGIC_BYTES_PLZ
        + save_type.to_bytes(1, byteorder="little")
        + compressed_data
    )