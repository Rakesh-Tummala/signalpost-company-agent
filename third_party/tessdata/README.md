# Bundled Tesseract language data

`nor.traineddata` is the Norwegian language model for Tesseract OCR, from the
[tesseract-ocr/tessdata](https://github.com/tesseract-ocr/tessdata) project, licensed under the
Apache License 2.0. It is used only by the annual-report OCR stage
(`scripts/run_annual_report_workforce_connector.py`) when the machine's own Tesseract install has no
`nor` pack. The Tesseract program itself is not bundled: install `tesseract-ocr` and `poppler-utils`
(`pdftoppm`) from your package manager; without them that one stage degrades to "no workforce data".
