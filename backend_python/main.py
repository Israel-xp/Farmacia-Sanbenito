from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime

# Importamos tu base de datos
from database import SessionLocal, Medicamento, Lote, Venta, DetalleVenta

app = FastAPI(title="Farmacia Sanbenito API", version="2.0")

# Configuración CORS para que la página web pueda conectarse sin error
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dependencia para conectar a la Base de Datos en cada petición
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- MODELOS DE ENTRADA (PYDANTIC) ---
class VentaRequest(BaseModel):
    id_medicamento: int
    cantidad: int
    num_colegiado_medico: Optional[str] = None

# --- RUTAS DE LA API ---
@app.get("/")
def inicio():
    return {"mensaje": "Servidor Farmacia Sanbenito conectado a XAMPP"}

@app.get("/api/medicamentos/buscar/{codigo}")
def buscar_medicamento(codigo: str, db: Session = Depends(get_db)):
    # Buscar el medicamento en MySQL
    med = db.query(Medicamento).filter(Medicamento.codigo_barras == codigo).first()
    if not med:
        raise HTTPException(status_code=404, detail="Medicamento no encontrado en el sistema")
    
    # Calcular stock total sumando todos sus lotes disponibles
    stock_total = db.query(func.sum(Lote.stock)).filter(Lote.medicamento_id == med.id).scalar() or 0
    
    # Obtener el lote más próximo a vencer (Algoritmo FEFO - Visualización)
    lote_proximo = db.query(Lote).filter(Lote.medicamento_id == med.id, Lote.stock > 0).order_by(Lote.fecha_vencimiento.asc()).first()
    lote_str = lote_proximo.codigo_lote if lote_proximo else "Sin Lotes"
    fecha_venc_str = lote_proximo.fecha_vencimiento.strftime("%Y-%m-%d") if lote_proximo else "N/A"

    return {
        "id": med.id,
        "codigo_barras": med.codigo_barras,
        "nombre": med.nombre,
        "requiere_receta": med.requiere_receta,
        "precio": med.precio,
        "lote": lote_str,
        "fecha_vencimiento": fecha_venc_str,
        "stock": stock_total
    }

@app.post("/api/ventas/procesar")
def procesar_venta(venta_req: VentaRequest, db: Session = Depends(get_db)):
    med = db.query(Medicamento).filter(Medicamento.id == venta_req.id_medicamento).first()
    if not med:
        raise HTTPException(status_code=404, detail="Medicamento no encontrado")

    # 1. Validación de Receta
    if med.requiere_receta and not venta_req.num_colegiado_medico:
        raise HTTPException(status_code=400, detail="Este medicamento requiere receta médica y número de colegiado.")

    # 2. Validación de Stock Total
    stock_total = db.query(func.sum(Lote.stock)).filter(Lote.medicamento_id == med.id).scalar() or 0
    if stock_total < venta_req.cantidad:
        raise HTTPException(status_code=400, detail="Stock insuficiente en el inventario general.")

    # 3. ALGORITMO FEFO (First Expire, First Out)
    lotes_disponibles = db.query(Lote).filter(
        Lote.medicamento_id == med.id, 
        Lote.stock > 0
    ).order_by(Lote.fecha_vencimiento.asc()).all()

    cantidad_restante = venta_req.cantidad
    total_a_pagar = med.precio * venta_req.cantidad

    # Crear el registro principal de la Venta
    nueva_venta = Venta(
        total_pagado=total_a_pagar,
        num_colegiado_medico=venta_req.num_colegiado_medico
    )
    db.add(nueva_venta)
    db.flush()

    # Descontar lote por lote según FEFO
    for lote in lotes_disponibles:
        if cantidad_restante <= 0:
            break
            
        if lote.stock >= cantidad_restante:
            descuento = cantidad_restante
            lote.stock -= cantidad_restante
            cantidad_restante = 0
        else:
            descuento = lote.stock
            cantidad_restante -= lote.stock
            lote.stock = 0
            
        detalle = DetalleVenta(
            venta_id=nueva_venta.id,
            lote_id=lote.id,
            cantidad=descuento,
            precio_unitario=med.precio,
            subtotal=descuento * med.precio
        )
        db.add(detalle)

    db.commit()
    nuevo_stock = stock_total - venta_req.cantidad

    return {
        "estatus": "Venta procesada con FEFO exitosamente",
        "producto": med.nombre,
        "cantidad": venta_req.cantidad,
        "precio_unitario": med.precio,
        "total_pagado": total_a_pagar,
        "stock_restante": nuevo_stock,
        "colegiado_medico": venta_req.num_colegiado_medico if med.requiere_receta else "N/A"
    }