"""
Commercial Dashboard API endpoints
Provides data management for the commercial dashboard
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from typing import Annotated, List, Optional
from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel

from app.api.dependencies import get_db
from app.core.security import get_nc_user_info
from app.db.models import User, CommercialConfig, CommercialSettings, CommercialDailyData

router = APIRouter()


# ============================================================
# SCHEMAS
# ============================================================

class DayData(BaseModel):
    contactos: int = 0
    reuniones: int = 0
    contratos: int = 0
    ventas: float = 0
    nuevos: int = 0
    l_nuevos: int = 0
    l_contactados: int = 0
    l_interesados: int = 0
    l_info: int = 0
    l_seg: int = 0
    l_pres: int = 0
    l_neg: int = 0
    l_cerrados: int = 0
    notas: str = ""


class ComercialData(BaseModel):
    id: str
    userId: int
    nombre: str
    email: Optional[str] = None
    teamId: Optional[int] = None
    role: Optional[str] = None
    metaClientes: int
    minInv: float
    comision: float
    dias: dict[str, DayData]


class ConfigData(BaseModel):
    year: int
    month: int
    metaMensual: float
    metaContactosDia: int
    metaReunionesDia: int
    metaContratosDia: int
    ticketPromedio: float
    metaClientesNuevosMes: int
    montoMinInversion: float
    pctComision: float
    umbralVerde: float
    umbralAmarillo: float
    negocio: Optional[str] = None


class StateData(BaseModel):
    config: ConfigData
    comerciales: List[ComercialData]


# ============================================================
# HELPER FUNCTIONS
# ============================================================

async def _get_current_user(authorization: str, db: Session):
    """Get current user from authorization header"""
    nc_data = await get_nc_user_info(authorization)
    user = db.query(User).filter(User.nc_user_id == nc_data["id"]).first()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return user


def _is_commercial_user(user: User) -> bool:
    """Check if user has commercial access (team_id=2 or admin/leader)"""
    COMMERCIAL_TEAM_ID = 2
    return (
        user.team_id == COMMERCIAL_TEAM_ID or 
        user.role in ["admin", "leader"]
    )


def _get_or_create_config(db: Session, year: int, month: int) -> CommercialConfig:
    """Get or create config for a specific period"""
    config = db.query(CommercialConfig).filter(
        and_(
            CommercialConfig.year == year,
            CommercialConfig.month == month
        )
    ).first()
    
    if not config:
        # Create default config
        config = CommercialConfig(
            year=year,
            month=month,
            meta_mensual=Decimal("200000"),
            meta_contactos_dia=25,
            meta_reuniones_dia=3,
            meta_contratos_dia=2,
            ticket_promedio=Decimal("50000"),
            meta_clientes_nuevos_mes=4,
            monto_min_inversion=Decimal("50000"),
            pct_comision=Decimal("2.0"),
            umbral_verde=Decimal("1.0"),
            umbral_amarillo=Decimal("0.8"),
            negocio="Fondo de inversión en Estados Unidos. Ticket mínimo de inversión: $50,000.",
            is_active=True
        )
        db.add(config)
        db.commit()
        db.refresh(config)
    
    return config


def _get_or_create_settings(db: Session, user_id: int, defaults: CommercialConfig) -> CommercialSettings:
    """Get or create commercial settings for a user"""
    settings = db.query(CommercialSettings).filter(
        CommercialSettings.user_id == user_id
    ).first()
    
    if not settings:
        settings = CommercialSettings(
            user_id=user_id,
            meta_clientes=defaults.meta_clientes_nuevos_mes,
            min_inv=defaults.monto_min_inversion,
            comision=defaults.pct_comision
        )
        db.add(settings)
        db.commit()
        db.refresh(settings)
    
    return settings


# ============================================================
# ENDPOINTS
# ============================================================

@router.get("/state")
async def get_state(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
    year: Optional[int] = None,
    month: Optional[int] = None
):
    """
    Get the complete state of the commercial dashboard
    Returns config and data for all commercial users
    """
    user = await _get_current_user(authorization, db)
    
    if not _is_commercial_user(user):
        raise HTTPException(status_code=403, detail="No access to commercial dashboard")
    
    # Use current period if not specified
    if year is None or month is None:
        today = date.today()
        year = today.year
        month = today.month  # Keep 1-12 format
    
    print(f"[DEBUG] GET /state - year: {year}, month: {month}")
    
    # Get or create config
    config = _get_or_create_config(db, year, month)
    
    print(f"[DEBUG] Config found: year={config.year}, month={config.month}")
    
    # Get all commercial users (team_id=2) or admins/leaders
    COMMERCIAL_TEAM_ID = 2
    users = db.query(User).filter(
        and_(
            User.is_active == True,
            or_(
                User.team_id == COMMERCIAL_TEAM_ID,  # Commercial team
                User.role.in_(["admin", "leader"])    # Or admin/leader
            )
        )
    ).all()
    
    print(f"[DEBUG] Found {len(users)} commercial users (team_id={COMMERCIAL_TEAM_ID} or admin/leader)")
    
    comerciales = []
    for u in users:
        # Get or create settings
        settings = _get_or_create_settings(db, u.id, config)
        
        # Get daily data
        daily_data = db.query(CommercialDailyData).filter(
            CommercialDailyData.user_id == u.id
        ).order_by(CommercialDailyData.date).all()
        
        print(f"[DEBUG] User {u.display_name} (id={u.id}) has {len(daily_data)} daily records")
        
        dias = {}
        for day in daily_data:
            date_key = day.date.isoformat()
            dias[date_key] = DayData(
                contactos=day.contactos,
                reuniones=day.reuniones,
                contratos=day.contratos,
                ventas=float(day.ventas),
                nuevos=day.clientes_nuevos,
                l_nuevos=day.leads_nuevos,
                l_contactados=day.leads_contactados,
                l_interesados=day.leads_interesados,
                l_info=day.leads_info_enviada,
                l_seg=day.leads_seguimiento,
                l_pres=day.leads_presentacion,
                l_neg=day.leads_negociacion,
                l_cerrados=day.leads_cerrados,
                notas=day.notas or ""
            )
        
        comerciales.append(ComercialData(
            id=f"u{u.id}",
            userId=u.id,
            nombre=u.display_name,
            email=u.email,
            teamId=u.team_id,
            role=u.role,
            metaClientes=settings.meta_clientes,
            minInv=float(settings.min_inv),
            comision=float(settings.comision),
            dias={k: v.model_dump() for k, v in dias.items()}
        ))
    
    state = StateData(
        config=ConfigData(
            year=config.year,
            month=config.month,
            metaMensual=float(config.meta_mensual),
            metaContactosDia=config.meta_contactos_dia,
            metaReunionesDia=config.meta_reuniones_dia,
            metaContratosDia=config.meta_contratos_dia,
            ticketPromedio=float(config.ticket_promedio),
            metaClientesNuevosMes=config.meta_clientes_nuevos_mes,
            montoMinInversion=float(config.monto_min_inversion),
            pctComision=float(config.pct_comision),
            umbralVerde=float(config.umbral_verde),
            umbralAmarillo=float(config.umbral_amarillo),
            negocio=config.negocio
        ),
        comerciales=comerciales
    )
    
    return {
        "payload": state.model_dump_json(),
        "ts": int(datetime.now().timestamp() * 1000)
    }


@router.post("/state")
async def save_state(
    state: StateData,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """
    Save the complete state of the commercial dashboard
    Admins can modify config and all comerciales' data
    Comerciales can only modify their own daily data
    """
    user = await _get_current_user(authorization, db)
    
    if not _is_commercial_user(user):
        raise HTTPException(status_code=403, detail="No access to commercial dashboard")
    
    is_admin = user.role == "admin"
    
    print(f"[DEBUG] POST /state - user: {user.display_name} (id={user.id}), role: {user.role}, is_admin: {is_admin}")
    print(f"[DEBUG] POST /state - comerciales count: {len(state.comerciales)}")
    
    try:
        # Update config (admin only)
        if is_admin:
            cfg = state.config
            config = db.query(CommercialConfig).filter(
                and_(
                    CommercialConfig.year == cfg.year,
                    CommercialConfig.month == cfg.month
                )
            ).first()
            
            if config:
                config.meta_mensual = Decimal(str(cfg.metaMensual))
                config.meta_contactos_dia = cfg.metaContactosDia
                config.meta_reuniones_dia = cfg.metaReunionesDia
                config.meta_contratos_dia = cfg.metaContratosDia
                config.ticket_promedio = Decimal(str(cfg.ticketPromedio))
                config.meta_clientes_nuevos_mes = cfg.metaClientesNuevosMes
                config.monto_min_inversion = Decimal(str(cfg.montoMinInversion))
                config.pct_comision = Decimal(str(cfg.pctComision))
                config.umbral_verde = Decimal(str(cfg.umbralVerde))
                config.umbral_amarillo = Decimal(str(cfg.umbralAmarillo))
                config.negocio = cfg.negocio
            else:
                config = CommercialConfig(**cfg.model_dump())
                db.add(config)
        
        # Update comerciales data
        for comercial in state.comerciales:
            user_id = comercial.userId
            
            print(f"[DEBUG] POST - Processing comercial: {comercial.nombre} (userId={user_id}), dias count: {len(comercial.dias)}")
            
            # Non-admin users can only update their own data
            if not is_admin and user_id != user.id:
                print(f"[DEBUG] POST - SKIPPED (not admin and not own data)")
                continue  # Skip other users' data
            
            # Update settings (admin only)
            if is_admin:
                settings = db.query(CommercialSettings).filter(
                    CommercialSettings.user_id == user_id
                ).first()
                
                if settings:
                    settings.meta_clientes = comercial.metaClientes
                    settings.min_inv = Decimal(str(comercial.minInv))
                    settings.comision = Decimal(str(comercial.comision))
                else:
                    settings = CommercialSettings(
                        user_id=user_id,
                        meta_clientes=comercial.metaClientes,
                        min_inv=Decimal(str(comercial.minInv)),
                        comision=Decimal(str(comercial.comision))
                    )
                    db.add(settings)
            
            # Update daily data
            for date_key, day_data in comercial.dias.items():
                # date_key format: "2026-06-08" (ISO format, 1-12 months)
                parts = date_key.split('-')
                year = int(parts[0])
                month = int(parts[1])  # Already 1-12 format
                day = int(parts[2])
                
                daily = db.query(CommercialDailyData).filter(
                    and_(
                        CommercialDailyData.user_id == user_id,
                        CommercialDailyData.date == date_key
                    )
                ).first()
                
                print(f"[DEBUG] POST - Saving daily data: user_id={user_id}, date={date_key}, contactos={day_data.contactos}")
                
                if daily:
                    print(f"[DEBUG] POST - Updating existing record")

                    daily.contactos = day_data.contactos
                    daily.reuniones = day_data.reuniones
                    daily.contratos = day_data.contratos
                    daily.ventas = Decimal(str(day_data.ventas))
                    daily.clientes_nuevos = day_data.nuevos
                    daily.leads_nuevos = day_data.l_nuevos
                    daily.leads_contactados = day_data.l_contactados
                    daily.leads_interesados = day_data.l_interesados
                    daily.leads_info_enviada = day_data.l_info
                    daily.leads_seguimiento = day_data.l_seg
                    daily.leads_presentacion = day_data.l_pres
                    daily.leads_negociacion = day_data.l_neg
                    daily.leads_cerrados = day_data.l_cerrados
                    daily.notas = day_data.notas
                else:
                    print(f"[DEBUG] POST - Creating new record")
                    daily = CommercialDailyData(
                        user_id=user_id,
                        date=date_key,
                        year=year,
                        month=month,
                        day=day,
                        contactos=day_data.contactos,
                        reuniones=day_data.reuniones,
                        contratos=day_data.contratos,
                        ventas=Decimal(str(day_data.ventas)),
                        clientes_nuevos=day_data.nuevos,
                        leads_nuevos=day_data.l_nuevos,
                        leads_contactados=day_data.l_contactados,
                        leads_interesados=day_data.l_interesados,
                        leads_info_enviada=day_data.l_info,
                        leads_seguimiento=day_data.l_seg,
                        leads_presentacion=day_data.l_pres,
                        leads_negociacion=day_data.l_neg,
                        leads_cerrados=day_data.l_cerrados,
                        notas=day_data.notas
                    )
                    db.add(daily)
        
        db.commit()
        
        print(f"[DEBUG] POST - Successfully committed to database")
        
        return {
            "success": True,
            "message": "Estado guardado correctamente",
            "ts": int(datetime.now().timestamp() * 1000)
        }
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/comercial/{comercial_id}")
async def get_comercial(
    comercial_id: str,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """
    Get data for a specific comercial
    """
    user = await _get_current_user(authorization, db)
    
    if not _is_commercial_user(user):
        raise HTTPException(status_code=403, detail="No access to commercial dashboard")
    
    # Extract user_id from format "u123" -> 123
    user_id = int(comercial_id.replace("u", ""))
    
    # Check if requesting own data or is admin
    if user.id != user_id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Can only access own data")
    
    comercial = db.query(User).filter(User.id == user_id).first()
    if not comercial:
        raise HTTPException(status_code=404, detail="Comercial not found")
    
    # Get active config
    config = db.query(CommercialConfig).filter(
        CommercialConfig.is_active == True
    ).order_by(CommercialConfig.year.desc(), CommercialConfig.month.desc()).first()
    
    if not config:
        raise HTTPException(status_code=404, detail="No active configuration found")
    
    settings = _get_or_create_settings(db, user_id, config)
    
    # Get daily data
    daily_data = db.query(CommercialDailyData).filter(
        CommercialDailyData.user_id == user_id
    ).order_by(CommercialDailyData.date).all()
    
    dias = {}
    for day in daily_data:
        date_key = day.date.isoformat()
        dias[date_key] = {
            "contactos": day.contactos,
            "reuniones": day.reuniones,
            "contratos": day.contratos,
            "ventas": float(day.ventas),
            "nuevos": day.clientes_nuevos,
            "l_nuevos": day.leads_nuevos,
            "l_contactados": day.leads_contactados,
            "l_interesados": day.leads_interesados,
            "l_info": day.leads_info_enviada,
            "l_seg": day.leads_seguimiento,
            "l_pres": day.leads_presentacion,
            "l_neg": day.leads_negociacion,
            "l_cerrados": day.leads_cerrados,
            "notas": day.notas or ""
        }
    
    return {
        "id": f"u{comercial.id}",
        "userId": comercial.id,
        "nombre": comercial.display_name,
        "email": comercial.email,
        "metaClientes": settings.meta_clientes,
        "minInv": float(settings.min_inv),
        "comision": float(settings.comision),
        "dias": dias
    }
