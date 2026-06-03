import time
import random
from datetime import datetime

class EstadoSimulador:
    def __init__(self):
        # Nominales y Físicos
        self.v_fase_nom = 13200
        self.i_nom = 105
        self.fp_carga = 0.95
        self.carga_actual = 1.0
        
        # Máquina de Estados (ANSI 79)
        self.estado_fsm = "CERRADO"
        self.tiempo_apertura = 0.0
        self.intentos = 0
        self.max_intentos = 3
        self.dead_time = 5.0
        
        # Flags de Inyección de Fallas
        self.falla_permanente = False
        self.falla_transitoria = False
        self.falla_tension = False # SAG
        self.tiempo_inicio_sag = 0.0
        self.duracion_sag = 0.0
        
        # NUEVAS FALLAS Y TELECONTROL
        self.falla_fase_a = False
        self.falla_fase_b = False
        self.falla_fase_c = False
        self.apertura_manual = False
        
        # Telemetría Modbus
        self.pickup = False
        self.open_prot = False
        self.bloqueo_lockout = False
        self.alarma_general = False
        self.alarma_tension = False
        self.operaciones_totales = 0  # Inicializado en 0 para ThingsBoard
        self.energia_activa_acumulada = 100000.0
        self.energia_reactiva_acumulada = 25000.0
        
        self.eventos_pendientes = []

    def log_evento(self, mensaje):
        self.eventos_pendientes.append(mensaje)

    def procesar_ciclo(self):
        t_actual = time.time()
        hora = datetime.now().hour
        self.carga_actual = 1.0 if hora in [13, 14, 20, 21, 22] else (0.4 if 2 <= hora <= 5 else 0.7)
        
        # ==============================================================
        # TELECONTROL Y MÁQUINA DE ESTADOS
        # ==============================================================
        falla_activa = (self.falla_permanente or self.falla_transitoria or 
                        self.falla_fase_a or self.falla_fase_b or self.falla_fase_c)

        if self.estado_fsm == "CERRADO":
            if self.apertura_manual:
                self.estado_fsm = "ABIERTO_MANUAL"
                self.log_evento("🔒 TELECONTROL: Apertura manual ejecutada por SCADA.")
                self.operaciones_totales += 1
            elif falla_activa:
                self.estado_fsm = "ESPERA"
                self.pickup = True
                self.open_prot = True
                self.tiempo_apertura = t_actual
                self.operaciones_totales += 1
                self.log_evento(f"⚠️ DISPARO: Anomalía en red. Abriendo contactos. (Op: {self.operaciones_totales})")
            else:
                self.intentos = 0
                self.pickup = False
                
        elif self.estado_fsm == "ABIERTO_MANUAL":
            if not self.apertura_manual:
                self.estado_fsm = "CERRADO"
                self.log_evento("🔓 TELECONTROL: Cierre manual ejecutado. Línea energizada.")
                self.operaciones_totales += 1
                
        elif self.estado_fsm == "ESPERA":
            if t_actual - self.tiempo_apertura >= self.dead_time:
                # Limitamos el contador para que nunca pase del máximo (3)
                if self.intentos < self.max_intentos:
                    self.intentos += 1
                    
                if self.intentos >= self.max_intentos:
                    self.estado_fsm = "BLOQUEO"
                    self.bloqueo_lockout = True
                    self.alarma_general = True
                    self.log_evento("❌ BLOQUEO: Intentos agotados. Falla permanente/Desbalance.")
                else:
                    self.estado_fsm = "CERRADO"
                    self.open_prot = False
                    self.log_evento(f"🔄 REENGANCHE: Intento {self.intentos}/{self.max_intentos}. Cerrando contactos.")
                    if self.falla_transitoria:
                        self.falla_transitoria = False
                        self.log_evento("✅ Falla transitoria despejada.")
                        
        elif self.estado_fsm == "BLOQUEO":
            # REGLA DE SEGURIDAD ANSI 79:
            # El equipo jamás sale de BLOQUEO de forma automática.
            # Debe recibir un comando RESET explícito desde la API.
            pass

        # ==============================================================
        # GESTIÓN DE SAG DE TENSIÓN
        # ==============================================================
        if self.falla_tension:
            if self.tiempo_inicio_sag == 0.0:
                self.tiempo_inicio_sag = t_actual
                self.duracion_sag = random.uniform(1.0, 5.0) 
                self.alarma_tension = True
                self.log_evento(f"⚡ ALARMA: SAG de tensión detectado.")
            elif t_actual - self.tiempo_inicio_sag >= self.duracion_sag:
                self.falla_tension = False
                self.alarma_tension = False
                self.tiempo_inicio_sag = 0.0
                self.log_evento("✅ SAG superado. Red normalizada.")
        else:
            self.tiempo_inicio_sag = 0.0
            self.alarma_tension = False