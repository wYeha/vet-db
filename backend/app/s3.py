"""Генерация presigned S3 URL для оригиналов PDF.

Ключ (`pdf_s3key`) передаётся в S3 в исходном виде — boto3 корректно подписывает
не-ASCII символы и пробелы. Клиент S3 создаётся лениво, чтобы приложение
поднималось даже без заданных ключей (эндпоинт /pdf тогда вернёт 503).
"""
from functools import lru_cache

import boto3
from botocore.config import Config

from . import config


class S3NotConfigured(RuntimeError):
    """S3-ключи не заданы в окружении."""


@lru_cache(maxsize=1)
def _client():
    if not (config.S3_ACCESS_KEY and config.S3_SECRET_KEY):
        raise S3NotConfigured("S3 credentials (AK/SK) are not set in environment")
    return boto3.client(
        "s3",
        endpoint_url=config.S3_ENDPOINT,
        aws_access_key_id=config.S3_ACCESS_KEY,
        aws_secret_access_key=config.S3_SECRET_KEY,
        region_name=config.S3_REGION,
        config=Config(signature_version="s3v4"),
    )


def presign_pdf(s3key: str, ttl: int | None = None) -> str:
    """Возвращает presigned URL на объект в бакете. Бросает S3NotConfigured, если нет ключей."""
    ttl = ttl or config.PDF_URL_TTL
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": config.S3_BUCKET, "Key": s3key},
        ExpiresIn=ttl,
    )
