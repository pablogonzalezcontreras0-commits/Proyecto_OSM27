import asyncio
import os
import sys
import datetime
from pymodbus.client import AsyncModbusTcpClient
import httpx

RESET, ROJO, VERDE, AMARILLO, CIAN = "\033[0m", "\033[91m", "\033[92m", "\033[93m", "\033[96m"

datos_ui = {
    "ua":0, "ub":0, "uc":0, "ia":0, "ib":0, "ic":0, 
    "kw":0, "kvar":0, "kva":0, "fp":0, "frec":0.0,
    "energia_act":0, "energia_react":0, 
    "intentos":0, "operaciones":0, "max_intentos":3,
    "cerrado":False, "abierto":False, "bloqueo":False, "sag":False, 
    "fecha_equipo": "---", "msg":"Iniciando SCADA..."
}

conexion_activa = True

def dibujar_interfaz():
    sys.stdout.write('\033[H')
    sys.stdout.flush()
    
    led_c = f"{ROJO}⬤{RESET}" if datos_ui['cerrado'] else "◯" 
    led_a = f"{VERDE}⬤{RESET}" if datos_ui['abierto'] else "◯" 
    led_b = f"{AMARILLO}⬤{RESET}" if datos_ui['bloqueo'] else "◯"
    led_s = f"{AMARILLO}⬤{RESET}" if datos_ui['sag'] else "◯"
    
    estado_tcp = f"{VERDE}ONLINE{RESET}" if conexion_activa else f"{ROJO}OFFLINE{RESET}"
    
    print(f"{CIAN}=== TELEMETRÍA SCADA NOJA OSM27 ==={RESET} | RELOJ: {datos_ui['fecha_equipo']}\033[K")
    print(f" RED MODBUS: {estado_tcp} | ESTADOS: {led_c} CERRADO {led_a} ABIERTO {led_b} BLOQUEO {led_s} SAG\033[K")
    print("------------------------------------------------------------------------\033[K")
    print(f" Ua: {datos_ui['ua']:>5.2f}kV | Ia: {datos_ui['ia']:>4d}A | P: {datos_ui['kw']:>4d}kW   | FP: {datos_ui['fp']:.2f} | E.Act: {datos_ui['energia_act']} kWh\033[K")
    print(f" Ub: {datos_ui['ub']:>5.2f}kV | Ib: {datos_ui['ib']:>4d}A | Q: {datos_ui['kvar']:>4d}kVAr | Hz: {datos_ui['frec']:.2f} | E.Rea: {datos_ui['energia_react']} kVARh\033[K")
    print(f" Uc: {datos_ui['uc']:>5.2f}kV | Ic: {datos_ui['ic']:>4d}A | S: {datos_ui['kva']:>4d}kVA  | AR: {datos_ui['intentos']}/3  | Ops: {datos_ui['operaciones']}\033[K")
    print("------------------------------------------------------------------------\033[K")
    print(f"{AMARILLO} ÚLTIMO EVENTO: {datos_ui['msg']}{RESET}\033[K")
    print(f"{CIAN}========================================================================{RESET}\033[K")
    print(" [1] Transitoria    [2] Permanente     [3] Hueco Tensión (Sag)\033[K")
    print(" [4] Pérdida Fase A [5] Pérdida Fase B [6] Pérdida Fase C\033[K")
    print(" [7] Abrir (Manual) [8] Cerrar (Manual)[0] RESET/LIMPIAR FALLAS\033[K")
    print(" [C] Conectar/Cortar TCP               [Q] Salir del Monitor\033[K")
    print(" > Comando: \033[K", end="", flush=True)

async def tarea_modbus():
    cliente = AsyncModbusTcpClient('127.0.0.1', port=502)
    await cliente.connect()
    
    # Limpieza dura inicial
    os.system('cls' if os.name == 'nt' else 'clear')
    
    while True:
        if conexion_activa:
            if not cliente.connected: await cliente.connect()
            try:
                r_blk = await cliente.read_discrete_inputs(10001, 1, slave=1)
                r_ab = await cliente.read_discrete_inputs(10035, 1, slave=1)
                r_sag = await cliente.read_discrete_inputs(10040, 1, slave=1)
                r_cer = await cliente.read_discrete_inputs(10075, 1, slave=1)
                
                r_i = await cliente.read_input_registers(30001, 3, slave=1)
                r_v = await cliente.read_input_registers(30005, 3, slave=1)
                r_p = await cliente.read_input_registers(30026, 3, slave=1)
                r_e1 = await cliente.read_input_registers(30041, 2, slave=1)
                r_e2 = await cliente.read_input_registers(30043, 2, slave=1)
                r_frec = await cliente.read_input_registers(30061, 1, slave=1)
                r_fp = await cliente.read_input_registers(30068, 1, slave=1)
                r_int = await cliente.read_input_registers(30075, 2, slave=1)
                r_time = await cliente.read_holding_registers(40001, 2, slave=1)

                if not any(r.isError() for r in [r_blk, r_ab, r_sag, r_cer, r_i, r_v, r_p, r_frec, r_fp, r_e1, r_e2, r_time, r_int]):
                    datos_ui["bloqueo"] = r_blk.bits[0]
                    datos_ui["abierto"] = r_ab.bits[0]
                    datos_ui["sag"] = r_sag.bits[0]
                    datos_ui["cerrado"] = r_cer.bits[0]
                    
                    datos_ui["ia"], datos_ui["ib"], datos_ui["ic"] = r_i.registers
                    datos_ui["ua"] = r_v.registers[0] / 1000.0
                    datos_ui["ub"] = r_v.registers[1] / 1000.0
                    datos_ui["uc"] = r_v.registers[2] / 1000.0
                    
                    datos_ui["kva"], datos_ui["kvar"], datos_ui["kw"] = r_p.registers
                    datos_ui["frec"] = r_frec.registers[0] / 100.0
                    datos_ui["fp"] = r_fp.registers[0] / 1000.0
                    
                    datos_ui["operaciones"] = r_int.registers[0]
                    datos_ui["intentos"] = r_int.registers[1]
                    
                    datos_ui["energia_act"] = (r_e1.registers[0] << 16) | r_e1.registers[1]
                    datos_ui["energia_react"] = (r_e2.registers[0] << 16) | r_e2.registers[1]
                    
                    unix_ts = (r_time.registers[0] << 16) | r_time.registers[1]
                    datos_ui["fecha_equipo"] = datetime.datetime.fromtimestamp(unix_ts).strftime('%Y-%m-%d %H:%M:%S')
            except Exception as e:
                datos_ui["msg"] = f"Aviso Modbus: {e}"
        else:
            cliente.close()
            datos_ui["ua"] = datos_ui["ub"] = datos_ui["uc"] = 0.0
            datos_ui["ia"] = datos_ui["ib"] = datos_ui["ic"] = 0
            datos_ui["kw"] = datos_ui["kvar"] = datos_ui["kva"] = 0
            datos_ui["frec"] = 0.0
            datos_ui["fecha_equipo"] = "DESCONECTADO"
            
        dibujar_interfaz()
        await asyncio.sleep(1)

async def enviar_api(comando: str):
    datos_ui["msg"] = f"Procesando comando '{comando.upper()}'..."
    dibujar_interfaz()
    try:
        async with httpx.AsyncClient() as client:
            await client.post(f"http://127.0.0.1:8000/api/evento/{comando}")
            datos_ui["msg"] = f"Comando '{comando.upper()}' inyectado exitosamente."
    except Exception as e:
        datos_ui["msg"] = f"Error conectando a API: {e}"
    dibujar_interfaz()

async def tarea_teclado():
    global conexion_activa
    loop = asyncio.get_running_loop()
    while True:
        cmd = await loop.run_in_executor(None, sys.stdin.readline)
        cmd = cmd.strip().upper()
        
        # ========================================================
        # LA SOLUCIÓN: Limpieza total nativa al presionar ENTER
        # Esto mata el desplazamiento provocado por el "echo" de la tecla
        # ========================================================
        os.system('cls' if os.name == 'nt' else 'clear')

        if cmd == '1': await enviar_api("transitoria")
        elif cmd == '2': await enviar_api("permanente")
        elif cmd == '3': await enviar_api("sag")
        elif cmd == '4': await enviar_api("fase_a")
        elif cmd == '5': await enviar_api("fase_b")
        elif cmd == '6': await enviar_api("fase_c")
        elif cmd == '7': await enviar_api("abrir")
        elif cmd == '8': await enviar_api("cerrar")
        elif cmd == '0': await enviar_api("reset")
        elif cmd == 'C': 
            conexion_activa = not conexion_activa
            datos_ui["msg"] = "Enlace TCP Modbus restablecido." if conexion_activa else "Enlace TCP cortado intencionalmente."
            dibujar_interfaz()
        elif cmd == 'Q': 
            os.system('cls' if os.name == 'nt' else 'clear')
            print("Saliendo del Monitor SCADA...")
            os._exit(0)
        else:
            dibujar_interfaz()

async def main():
    await asyncio.gather(tarea_modbus(), tarea_teclado())

if __name__ == "__main__":
    asyncio.run(main())