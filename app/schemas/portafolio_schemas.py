"""Schemas de portafolios. Ubicar en app/schemas/portafolio_schemas.py"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.schemas.base import UTCModel


class PortafolioOut(UTCModel):
    """Salida hacia el frontend. from_attributes permite serializar el modelo
    ORM directo; las fechas salen en RFC3339 con 'Z' (heredado de UTCModel)."""
    id: int
    nombre: str
    monto_minimo: Decimal
    rendimiento_anual: Decimal
    nivel_riesgo: Decimal
    orden: int
    activo: bool
    notas: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PortafolioCreate(BaseModel):
    """Entrada de creación. Acepta camelCase (montoMinimo, …) o snake_case.
    La validación reemplaza a los CHECK que no pusimos en la BD."""
    nombre: str = Field(min_length=1, max_length=100)
    monto_minimo: Decimal = Field(ge=0)
    rendimiento_anual: Decimal                       # puede ser negativo
    nivel_riesgo: Decimal = Field(ge=0, le=100)
    orden: int = 0
    activo: bool = True
    notas: Optional[str] = None

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )


class PortafolioUpdate(BaseModel):
    """Entrada de edición parcial (PATCH). Solo se aplican los campos enviados."""
    nombre: Optional[str] = Field(default=None, min_length=1, max_length=100)
    monto_minimo: Optional[Decimal] = Field(default=None, ge=0)
    rendimiento_anual: Optional[Decimal] = None
    nivel_riesgo: Optional[Decimal] = Field(default=None, ge=0, le=100)
    orden: Optional[int] = None
    activo: Optional[bool] = None
    notas: Optional[str] = None

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )

class RentabilidadYearOut(BaseModel):
    """Un año con solo los meses que tienen valor: {1: 3.32, 3: -0.84, ...}.
    El total del año no viaja: lo calcula el front."""
    anio: int
    meses: Dict[int, Decimal]
 
 
class RentabilidadYearIn(BaseModel):
    """Guarda un año completo. Envía los meses a fijar; un mes en null (o
    ausente) borra ese mes (queda vacío = cuenta como 0)."""
    meses: Dict[int, Optional[Decimal]]
 
    model_config = ConfigDict(extra="ignore")