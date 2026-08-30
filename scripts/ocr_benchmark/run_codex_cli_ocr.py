"""Compatibility entrypoint for the OpenAI CLI/API OCR benchmark."""

from run_openai_ocr_benchmark import main


if __name__ == "__main__":
    main(default_surface="cli")
