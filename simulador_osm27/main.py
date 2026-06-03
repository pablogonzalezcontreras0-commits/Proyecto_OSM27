import asyncio
import logging
import random
import math
import uvicorn
import time
from fastapi import FastAPI, Request
from pymodbus.server import StartAsyncTcpServer
from datastore import inicializar_memoria_noja
from estado import EstadoSimulador

logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger(__name__)

app = FastAPI(title="Simulador OSM27 Avanzado")
cola_alertas = asyncio.Queue()

async def worker_notificaciones():
    while True:
        alerta = await cola_alertas.get()
        _logger.warning(f"🚀 [ALERTA MULTICANAL DESPACHADA] -> {alerta}")
        cola_alertas.task_done()

def c16(valor):
    return min(65535, max(0, int(valor)))

async def motor_matematico(esclavo, estado_global):
    _logger.info("Motor matemático iniciado.")
    while True:
        estado_global.procesar_ciclo()
        
        while estado_global.eventos_pendientes:
            await cola_alertas.put(estado_global.eventos_pendientes.pop(0))

        # 1. CAPA FÍSICA (Tensiones y Corrientes con AWGN + Pérdida de Fase)
        v_base = estado_global.v_fase_nom * (0.8 if estado_global.falla_tension else 1.0)
        
        ua = 0 if estado_global.falla_fase_a else int(random.gauss(v_base, 0.01 * v_base))
        ub = 0 if estado_global.falla_fase_b else int(random.gauss(v_base, 0.01 * v_base))
        uc = 0 if estado_global.falla_fase_c else int(random.gauss(v_base, 0.01 * v_base))
        
        uab = int(ua * 1.732) if ua > 0 and ub > 0 else 0
        ubc = int(ub * 1.732) if ub > 0 and uc > 0 else 0
        uca = int(uc * 1.732) if uc > 0 and ua > 0 else 0

        if estado_global.estado_fsm == "CERRADO":
            i_base = estado_global.i_nom * estado_global.carga_actual
            if getattr(estado_global, 'pickup', False): 
                i_base = 8000
            
            ia = 0 if estado_global.falla_fase_a else int(random.gauss(i_base, 0.015 * i_base))
            ib = 0 if estado_global.falla_fase_b else int(random.gauss(i_base, 0.015 * i_base))
            ic = 0 if estado_global.falla_fase_c else int(random.gauss(i_base, 0.015 * i_base))
            
            kva_total = int(((ua * ia) + (ub * ib) + (uc * ic)) / 1000)
            
            fp_ruido = random.gauss(estado_global.fp_carga, 0.004)
            fp_ruido = min(1.0, max(0.0, fp_ruido))
            
            kw_total = int(kva_total * fp_ruido)
            kvar_total = int(kva_total * math.sin(math.acos(fp_ruido)))
            
            estado_global.energia_activa_acumulada += (kw_total / 3600.0)
            estado_global.energia_reactiva_acumulada += (kvar_total / 3600.0)
        else:
            ia = ib = ic = 0
            kva_total = kw_total = kvar_total = 0
            fp_ruido = 0.0

        frecuencia_ruido = int(random.gauss(50.0, 0.02) * 100)

        # 2. ESCRITURA MODBUS (Estándar Big-Endian Nativo)
        esclavo.setValues(2, 10001, [1 if estado_global.bloqueo_lockout else 0])
        esclavo.setValues(2, 10010, [1 if getattr(estado_global, 'pickup', False) else 0])
        esclavo.setValues(2, 10035, [1 if estado_global.estado_fsm != "CERRADO" else 0])
        esclavo.setValues(2, 10040, [1 if estado_global.alarma_tension else 0])
        esclavo.setValues(2, 10064, [1 if estado_global.alarma_general else 0])
        esclavo.setValues(2, 10075, [1 if estado_global.estado_fsm == "CERRADO" else 0])
        
        esclavo.setValues(4, 30001, [c16(ia), c16(ib), c16(ic)])
        esclavo.setValues(4, 30005, [c16(ua), c16(ub), c16(uc)])
        esclavo.setValues(4, 30011, [c16(uab), c16(ubc), c16(uca)])
        esclavo.setValues(4, 30026, [c16(kva_total), c16(kvar_total), c16(kw_total)])
        
        # Energía 32 Bits -> ESTÁNDAR BIG-ENDIAN (Hi-Lo)
        e_act = int(estado_global.energia_activa_acumulada)
        esclavo.setValues(4, 30041, [(e_act >> 16) & 0xFFFF, e_act & 0xFFFF])
        
        e_react = int(estado_global.energia_reactiva_acumulada)
        esclavo.setValues(4, 30043, [(e_react >> 16) & 0xFFFF, e_react & 0xFFFF])
        
        esclavo.setValues(4, 30061, [c16(frecuencia_ruido)])
        esclavo.setValues(4, 30068, [c16(fp_ruido * 1000)])
        
        # Parche de Operaciones e Intentos
        esclavo.setValues(4, 30075, [c16(estado_global.operaciones_totales)]) 
        esclavo.setValues(4, 30076, [c16(estado_global.intentos)]) 

        # Reloj 32 Bits -> ESTÁNDAR BIG-ENDIAN (Hi-Lo)
        t_unix = int(time.time())
        esclavo.setValues(3, 40001, [(t_unix >> 16) & 0xFFFF, t_unix & 0xFFFF])

        await asyncio.sleep(1)

@app.post("/api/evento/{tipo}")
async def inyectar_evento(tipo: str, request: Request):
    estado = request.app.state.estado
    # Inyección de fallas eléctricas
    if tipo == "transitoria": estado.falla_transitoria = True
    elif tipo == "permanente": estado.falla_permanente = True
    elif tipo == "sag": estado.falla_tension = True
    elif tipo == "fase_a": estado.falla_fase_a = True
    elif tipo == "fase_b": estado.falla_fase_b = True
    elif tipo == "fase_c": estado.falla_fase_c = True
    
    # Telecontrol y Reset
    elif tipo == "abrir": 
        estado.apertura_manual = True
        
    elif tipo == "cerrar": 
        # REGLA: Si está bloqueado, se rechaza el cierre manual por seguridad.
        if estado.estado_fsm == "BLOQUEO":
            estado.log_evento("⚠️ RECHAZO: El equipo está bloqueado por protección. Ejecute RESET (0) primero.")
        else:
            estado.apertura_manual = False
            
    elif tipo == "reset":
        # 1. Limpiamos todas las fallas físicas
        estado.falla_permanente = estado.falla_transitoria = estado.falla_tension = False
        estado.falla_fase_a = estado.falla_fase_b = estado.falla_fase_c = False
        
        # 2. Limpiamos los flags de alarma y Modbus (esto apaga los LEDs)
        estado.bloqueo_lockout = False
        estado.alarma_general = False
        estado.intentos = 0
        
        # 3. Forzamos el estado de Apertura Manual (para que no cierre automáticamente)
        estado.apertura_manual = True
        
        # 4. Transicionamos la máquina de estados
        if estado.estado_fsm == "BLOQUEO":
            estado.estado_fsm = "ABIERTO_MANUAL"
            estado.log_evento("🔧 RESET: Fallas y Bloqueo despejados. Equipo ABIERTO, esperando Cierre Manual (8).")
        else:
            estado.log_evento("🔧 RESET: Eventos limpiados.")
            
    return {"status": "ok", "evento": tipo}

async def main():
    contexto_servidor, esclavo_modbus = inicializar_memoria_noja()
    estado_global = EstadoSimulador()
    app.state.estado = estado_global
    
    servidor_modbus = StartAsyncTcpServer(context=contexto_servidor, address=("0.0.0.0", 502))
    config_uvicorn = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="error")
    
    await asyncio.gather(
        servidor_modbus,
        uvicorn.Server(config_uvicorn).serve(),
        motor_matematico(esclavo_modbus, estado_global),
        worker_notificaciones()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Simulador apagado.")