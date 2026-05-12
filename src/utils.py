from typing import overload

BASE64_FILE_MAGIC = [
    ("image/png", "iVBORw0KGgo"),
    ("image/jpeg", "/9j/4AAQ"),
    ("image/gif", "R0lGOD"),
    ("image/webp", "UklGR"),
]


def extract_batch_count_from_texts(texts: list[str], *, max_n: int = 0) -> int:
    batch_count = 1
    for text in texts:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() != "n":
                continue
            raw_count = value.strip()
            if not raw_count:
                continue
            if not raw_count.isdigit() or int(raw_count) < 1:
                raise ValueError("参数 n 必须是大于等于 1 的整数")
            batch_count = int(raw_count)

    if max_n > 0 and batch_count > max_n:
        raise ValueError(f"参数 n 不能超过 {max_n}")

    return batch_count


@overload
def get_base64_mime(b64str: str, default: None = None) -> str | None: ...
@overload
def get_base64_mime(b64str: str, default: str) -> str: ...
def get_base64_mime(b64str: str, default: str | None = None) -> str | None:
    for mime, magic in BASE64_FILE_MAGIC:
        if b64str.startswith(magic):
            return mime
    return default
