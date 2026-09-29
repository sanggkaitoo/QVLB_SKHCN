"""Lưu tệp tải lên có giới hạn dung lượng và định dạng."""
import os
import tempfile

from fastapi import HTTPException, UploadFile

_CHUNK = 1024 * 1024


async def save_upload(file: UploadFile, allowed_extensions: set[str], max_mb: int) -> str:
    """Stream the upload to a temp file; 415 for other formats, 413 above ``max_mb``. Caller deletes the file."""
    extension = os.path.splitext(file.filename or "")[1].lower()
    if extension not in allowed_extensions:
        raise HTTPException(415, "Định dạng tệp không được hỗ trợ. Chấp nhận: " + ", ".join(sorted(allowed_extensions)))
    limit = max_mb * 1024 * 1024
    size = 0
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=extension, prefix="docnexus_upload_")
    try:
        with handle:
            while chunk := await file.read(_CHUNK):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"Tệp vượt quá giới hạn {max_mb} MB.")
                handle.write(chunk)
        if size == 0:
            raise HTTPException(400, "Tệp rỗng.")
        return handle.name
    except BaseException:
        remove_quietly(handle.name)
        raise
    finally:
        await file.close()


def remove_quietly(path: str | None) -> None:
    if path:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
