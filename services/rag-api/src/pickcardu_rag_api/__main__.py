from pathlib import Path

import uvicorn
from dotenv import load_dotenv


def load_local_environment(env_file: Path | None = None) -> bool:
    service_root = Path(__file__).resolve().parents[2]
    repository_root = service_root.parents[1]
    return load_dotenv(env_file or repository_root / ".env", override=False)


def main() -> None:
    load_local_environment()
    uvicorn.run("pickcardu_rag_api.main:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
