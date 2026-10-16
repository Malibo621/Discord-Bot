"""قفل تشغيل واحد للبوت: يمنع تشغيل نسختين معًا (تضارب جلسات الصوت ورد كل أمر مرتين).

القفل على مستوى نظام التشغيل (يُحرَّر تلقائيًا عند انتهاء العملية حتى لو انهارت)، فلا يبقى قفل «عالق».
"""
from __future__ import annotations

import os
import sys

_handle = None  # يُبقي الملف مفتوحًا طوال عمر العملية


def acquire(path: str) -> bool:
    """يحاول أخذ القفل. يرجع False إن كانت نسخة أخرى من البوت تعمل بالفعل."""
    global _handle
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fh = open(path, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    _handle = fh
    return True


def release() -> None:
    global _handle
    if _handle is not None:
        try:
            if sys.platform == "win32":
                import msvcrt
                _handle.seek(0)
                msvcrt.locking(_handle.fileno(), msvcrt.LK_UNLCK, 1)
            _handle.close()
        except OSError:
            pass
        _handle = None
