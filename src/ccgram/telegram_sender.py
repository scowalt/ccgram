TELEGRAM_MAX_MESSAGE_LENGTH = 4096


def split_message(
    text: str, max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH
) -> list[str]:
    if len(text) <= max_length:
        return [text]

    chunks = []
    current_chunk = ""

    for line in text.split("\n"):
        if len(line) > max_length:
            if current_chunk:
                chunks.append(current_chunk.rstrip("\n"))
                current_chunk = ""
            for i in range(0, len(line), max_length):
                chunks.append(line[i : i + max_length])
        elif len(current_chunk) + len(line) + 1 > max_length:
            chunks.append(current_chunk.rstrip("\n"))
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"

    if current_chunk:
        chunks.append(current_chunk.rstrip("\n"))

    return chunks
