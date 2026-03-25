FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY m365_extract/ m365_extract/
RUN pip install --no-cache-dir ".[azure]"

FROM python:3.12-slim

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/m365-extract /usr/local/bin/m365-extract
COPY m365_extract/ m365_extract/

RUN useradd --create-home appuser
USER appuser

ENTRYPOINT ["m365-extract"]
