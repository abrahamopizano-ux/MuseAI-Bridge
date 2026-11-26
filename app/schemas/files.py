"""
Esquemas Pydantic con validación estricta para las operaciones de archivos del puente.
"""

from typing import Optional, List
from pydantic import BaseModel, Field


class FileListRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=500, description="Ruta de la carpeta autorizada a listar.")
    include_hidden: bool = Field(False, description="Si es True, incluye archivos ocultos.")


class FileItemInfo(BaseModel):
    name: str
    path: str
    is_dir: bool
    size_bytes: int
    modified_at: str
    extension: str


class FileListResponse(BaseModel):
    confirmed: bool = True
    path: str
    total_items: int
    items: List[FileItemInfo]


class FileReadRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=500, description="Ruta del archivo a leer.")
    encoding: str = Field("utf-8", description="Codificación de texto (por defecto utf-8).")
    max_bytes: int = Field(524288, ge=1024, le=5242880, description="Límite de lectura en bytes (máx 5 MB).")


class FileReadResponse(BaseModel):
    confirmed: bool = True
    path: str
    size_bytes: int
    sha256: str
    truncated: bool
    content: str


class FileWriteRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=500, description="Ruta del archivo a crear o editar.")
    content: str = Field(..., description="Contenido de texto a escribir en el archivo.")
    append: bool = Field(False, description="Si es True, agrega al final del archivo en lugar de sobrescribir.")
    encoding: str = Field("utf-8", description="Codificación de texto.")


class FileWriteResponse(BaseModel):
    confirmed: bool = True
    status: str
    path: str
    bytes_written: int
    file_size_on_disk: int
    sha256: str
    backup_created: bool
    backup_path: Optional[str] = None


class FilePatchEditRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=500, description="Ruta del archivo existente a modificar.")
    target_text: str = Field(..., min_length=1, description="Texto exacto que se desea buscar y reemplazar.")
    replacement_text: str = Field(..., description="Nuevo texto que reemplazará a target_text.")
    replace_all: bool = Field(False, description="Si es True, reemplaza todas las apariciones; si es False, solo la primera.")
    encoding: str = Field("utf-8", description="Codificación de texto.")


class FilePatchEditResponse(BaseModel):
    confirmed: bool = True
    status: str
    path: str
    replacements_made: int
    bytes_written: int
    sha256_before: str
    sha256_after: str
    backup_created: bool
    backup_path: str


class FileMkdirRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=500, description="Ruta de la carpeta a crear dentro de una ruta autorizada.")


class FileDeleteRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=500, description="Ruta del archivo o carpeta a enviar a la Papelera de Reciclaje.")


class FileMoveRequest(BaseModel):
    source_path: str = Field(..., min_length=1, max_length=500, description="Ruta origen dentro de carpetas autorizadas.")
    destination_path: str = Field(..., min_length=1, max_length=500, description="Ruta destino dentro de carpetas autorizadas.")
    overwrite: bool = Field(False, description="Si es True y el destino existe, crea un respaldo .bak antes de mover.")


class FileSearchRequest(BaseModel):
    base_path: str = Field(..., min_length=1, max_length=500, description="Carpeta autorizada donde iniciar la búsqueda.")
    query: str = Field(..., min_length=1, max_length=200, description="Texto o patrón glob (ej. '*.pdf' o 'reporte') a buscar en nombre y/o contenido.")
    search_by_name: bool = Field(True, description="Si es True, busca coincidencias en el nombre del archivo o carpeta.")
    search_by_content: bool = Field(True, description="Si es True, busca el texto también dentro del contenido de archivos legibles.")
    case_sensitive: bool = Field(False, description="Si es True, distingue mayúsculas y minúsculas.")
    max_results: int = Field(100, ge=1, le=500, description="Número máximo de coincidencias a devolver.")


class FileUploadRequest(BaseModel):
    destination_path: str = Field(..., min_length=1, max_length=500, description="Ruta completa del archivo destino dentro de carpetas autorizadas.")
    base64_data: str = Field(..., min_length=1, description="Contenido binario del archivo codificado en Base64.")


class FileDownloadRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=500, description="Ruta del archivo a descargar desde una carpeta autorizada.")


class FileDownloadResponse(BaseModel):
    confirmed: bool = True
    path: str
    filename: str
    size_bytes: int
    sha256: str
    mime_type: str
    base64_data: str
