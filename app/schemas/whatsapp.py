from pydantic import BaseModel, Field, field_validator
import re


class WhatsAppSendRequest(BaseModel):
    phone_number: str = Field(
        ...,
        example="+34612345678",
        description="Número de teléfono destino en formato internacional E.164 (con o sin '+')."
    )
    message: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="Cuerpo del mensaje de texto a enviar."
    )

    @field_validator("phone_number")
    @classmethod
    def validate_e164_format(cls, value: str) -> str:
        cleaned = re.sub(r"[\s\-\(\)]", "", value)
        if not re.match(r"^\+?[1-9]\d{7,14}$", cleaned):
            raise ValueError(
                "Formato de número telefónico inválido. Debe seguir el estándar E.164 (ej: +34612345678 o 5215512345678)."
            )
        return cleaned
