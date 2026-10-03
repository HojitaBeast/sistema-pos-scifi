import os
import sys
import sqlite3
import csv
import webbrowser
from threading import Timer
import webview
from threading import Thread
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory

# --- RESOLUCIÓN DE RUTAS PARA PYINSTALLER ---
def resource_path(relative_path):
    """ Obtiene la ruta absoluta para recursos embebidos en el .exe """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

def get_base_dir():
    """ Obtiene la ruta del directorio real donde está el ejecutable o script """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# Definir rutas persistentes en la carpeta del usuario/ejecutable
BASE_DIR = get_base_dir()
DB_NAME = os.path.join(BASE_DIR, 'pos_database.db')
CARPETA_HISTORIAL = os.path.join(BASE_DIR, 'historial_diario')

# Configurar Flask para ubicar 'templates' y 'static' embebidos
app = Flask(__name__,
            template_folder=resource_path('templates'),
            static_folder=resource_path('static'))

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Tabla de Productos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo_barras TEXT UNIQUE NOT NULL,
            nombre TEXT NOT NULL,
            marca TEXT,
            categoria TEXT DEFAULT 'Abarrotes',
            precio REAL NOT NULL,
            stock INTEGER NOT NULL
        )
    ''')

    # Crear índice en codigo_barras para acelerar búsquedas
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_prod_codigo ON productos(codigo_barras)")

    try:
        cursor.execute("ALTER TABLE productos ADD COLUMN categoria TEXT DEFAULT 'Abarrotes'")
    except sqlite3.OperationalError:
        pass

    # Tabla de Ventas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_hora TEXT NOT NULL,
            metodo_pago TEXT NOT NULL,
            total REAL NOT NULL,
            estado TEXT DEFAULT 'COMPLETADA'
        )
    ''')

    try:
        cursor.execute("ALTER TABLE ventas ADD COLUMN estado TEXT DEFAULT 'COMPLETADA'")
    except sqlite3.OperationalError:
        pass

    # Tabla de Detalle de Ventas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS detalle_ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venta_id INTEGER NOT NULL,
            producto_id INTEGER NOT NULL,
            cantidad INTEGER NOT NULL,
            precio_unitario REAL NOT NULL,
            subtotal REAL NOT NULL,
            FOREIGN KEY (venta_id) REFERENCES ventas (id),
            FOREIGN KEY (producto_id) REFERENCES productos (id)
        )
    ''')

    # Tabla de Configuración
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS configuracion (
            clave TEXT PRIMARY KEY,
            valor TEXT
        )
    ''')
    cursor.execute("INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ('pass_admin', '1234')")
    
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)

@app.route('/api/productos', methods=['GET'])
def get_productos():
    conn = get_db_connection()
    productos = conn.execute('SELECT * FROM productos ORDER BY nombre ASC').fetchall()
    conn.close()
    return jsonify({'success': True, 'productos': [dict(p) for p in productos]})

@app.route('/api/producto/<criterio>', methods=['GET'])
def buscar_producto(criterio):
    conn = get_db_connection()
    producto = conn.execute(
        'SELECT * FROM productos WHERE codigo_barras = ? OR nombre LIKE ? LIMIT 1', 
        (criterio, f'%{criterio}%')
    ).fetchone()
    conn.close()

    if producto:
        return jsonify({'success': True, 'producto': dict(producto)})
    return jsonify({'success': False, 'message': 'PRODUCTO NO ENCONTRADO'}), 404

@app.route('/api/producto/guardar', methods=['POST'])
def guardar_producto():
    data = request.json or {}
    prod_id = data.get('id')
    codigo = str(data.get('codigo_barras') or '').strip()
    nombre = str(data.get('nombre') or '').strip()
    marca = str(data.get('marca') or '').strip()
    categoria = str(data.get('categoria') or 'Abarrotes').strip()
    
    raw_precio = data.get('precio')
    raw_stock = data.get('stock')

    if not nombre:
        return jsonify({'success': False, 'message': 'FALTA INGRESAR NOMBRE DEL PRODUCTO'}), 400

    try:
        precio = float(raw_precio)
        stock = int(raw_stock)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'VERIFIQUE EL PRECIO Y EL STOCK'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    if not codigo:
        if prod_id and str(prod_id).strip() != "":
            previo = cursor.execute('SELECT codigo_barras FROM productos WHERE id = ?', (prod_id,)).fetchone()
            codigo = previo['codigo_barras'] if previo else f"INT-{int(datetime.now().timestamp())}"
        else:
            ultimo = cursor.execute('SELECT MAX(id) as max_id FROM productos').fetchone()
            siguiente_id = (ultimo['max_id'] or 0) + 1 if ultimo and ultimo['max_id'] else 1
            codigo = f"INT-{siguiente_id:04d}"

    try:
        if prod_id and str(prod_id).strip() != "":
            cursor.execute('''
                UPDATE productos 
                SET codigo_barras = ?, nombre = ?, marca = ?, categoria = ?, precio = ?, stock = ?
                WHERE id = ?
            ''', (codigo, nombre, marca, categoria, precio, stock, prod_id))
        else:
            cursor.execute('''
                INSERT INTO productos (codigo_barras, nombre, marca, categoria, precio, stock)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (codigo, nombre, marca, categoria, precio, stock))

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'codigo_barras': codigo})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'success': False, 'message': 'EL CÓDIGO DE BARRAS YA EXISTE'}), 400
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'message': f'ERROR: {str(e)}'}), 500

@app.route('/api/producto/eliminar/<int:id_prod>', methods=['DELETE'])
def eliminar_producto(id_prod):
    conn = get_db_connection()
    conn.execute('DELETE FROM productos WHERE id = ?', (id_prod,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/venta', methods=['POST'])
def registrar_venta():
    data = request.json or {}
    metodo_pago = data.get('metodo_pago', 'Efectivo')
    items = data.get('items', [])
    total = data.get('total', 0)
    fecha_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not items:
        return jsonify({'success': False, 'message': 'EL CARRITO ESTÁ VACÍO'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    for item in items:
        prod = cursor.execute('SELECT stock, nombre FROM productos WHERE id = ?', (item['id'],)).fetchone()
        if not prod or prod['stock'] < item['cantidad']:
            conn.close()
            return jsonify({
                'success': False, 
                'message': f"STOCK INSUFICIENTE EN '{item['nombre']}'"
            }), 400

    cursor.execute('''
        INSERT INTO ventas (fecha_hora, metodo_pago, total, estado)
        VALUES (?, ?, ?, 'COMPLETADA')
    ''', (fecha_hora, metodo_pago, total))
    
    venta_id = cursor.lastrowid

    for item in items:
        subtotal = item['precio'] * item['cantidad']
        cursor.execute('''
            INSERT INTO detalle_ventas (venta_id, producto_id, cantidad, precio_unitario, subtotal)
            VALUES (?, ?, ?, ?, ?)
        ''', (venta_id, item['id'], item['cantidad'], item['precio'], subtotal))

        cursor.execute('''
            UPDATE productos SET stock = stock - ? WHERE id = ?
        ''', (item['cantidad'], item['id']))

    conn.commit()
    conn.close()
    return jsonify({'success': True, 'venta_id': venta_id, 'fecha_hora': fecha_hora})

@app.route('/api/ventas/historial', methods=['GET'])
def historial_ventas():
    conn = get_db_connection()
    query = '''
        SELECT 
            v.id,
            v.fecha_hora,
            v.metodo_pago,
            v.total,
            v.estado,
            GROUP_CONCAT(p.nombre, ', ') AS productos,
            SUM(dv.cantidad) AS total_cantidades
        FROM ventas v
        JOIN detalle_ventas dv ON v.id = dv.venta_id
        JOIN productos p ON dv.producto_id = p.id
        GROUP BY v.id
        ORDER BY v.id DESC
    '''
    ventas = conn.execute(query).fetchall()
    conn.close()
    return jsonify({'success': True, 'ventas': [dict(v) for v in ventas]})

@app.route('/api/venta/<int:venta_id>', methods=['GET'])
def obtener_detalle_venta(venta_id):
    conn = get_db_connection()
    venta = conn.execute('SELECT * FROM ventas WHERE id = ?', (venta_id,)).fetchone()
    if not venta:
        conn.close()
        return jsonify({'success': False, 'message': 'VENTA NO ENCONTRADA'}), 404
        
    detalles = conn.execute('''
        SELECT dv.*, p.nombre 
        FROM detalle_ventas dv
        JOIN productos p ON dv.producto_id = p.id
        WHERE dv.venta_id = ?
    ''', (venta_id,)).fetchall()
    conn.close()

    return jsonify({
        'success': True, 
        'venta': dict(venta), 
        'items': [dict(d) for d in detalles]
    })

@app.route('/api/venta/anular/<int:venta_id>', methods=['POST'])
def anular_venta(venta_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    venta = cursor.execute('SELECT estado FROM ventas WHERE id = ?', (venta_id,)).fetchone()
    if not venta:
        conn.close()
        return jsonify({'success': False, 'message': 'VENTA NO ENCONTRADA'}), 404

    if venta['estado'] == 'ANULADA':
        conn.close()
        return jsonify({'success': False, 'message': 'LA VENTA YA FUE ANULADA'}), 400

    detalles = cursor.execute('SELECT producto_id, cantidad FROM detalle_ventas WHERE venta_id = ?', (venta_id,)).fetchall()
    for item in detalles:
        cursor.execute('''
            UPDATE productos SET stock = stock + ? WHERE id = ?
        ''', (item['cantidad'], item['producto_id']))

    cursor.execute("UPDATE ventas SET estado = 'ANULADA' WHERE id = ?", (venta_id,))

    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'VENTA ANULADA CORRECTAMENTE'})

@app.route('/api/config/pass', methods=['GET', 'POST'])
def gestionar_password():
    conn = get_db_connection()
    cursor = conn.cursor()
    if request.method == 'GET':
        cfg = cursor.execute("SELECT valor FROM configuracion WHERE clave = 'pass_admin'").fetchone()
        conn.close()
        return jsonify({'success': True, 'password': cfg['valor'] if cfg else '1234'})
    else:
        nueva_clave = request.json.get('password', '').strip()
        if not nueva_clave:
            conn.close()
            return jsonify({'success': False, 'message': 'LA CONTRASEÑA NO PUEDE ESTAR VACÍA'}), 400
        cursor.execute("INSERT OR REPLACE INTO configuracion (clave, valor) VALUES ('pass_admin', ?)", (nueva_clave,))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'CONTRASEÑA ACTUALIZADA'})

@app.route('/api/salir', methods=['POST'])
def salir():
    conn = get_db_connection()
    fecha_hoy = datetime.now().strftime("%Y-%m-%d")
    
    ventas_hoy = conn.execute('''
        SELECT * FROM ventas WHERE fecha_hora LIKE ?
    ''', (f'{fecha_hoy}%',)).fetchall()
    
    if not os.path.exists(CARPETA_HISTORIAL):
        os.makedirs(CARPETA_HISTORIAL)

    txt_path = os.path.join(CARPETA_HISTORIAL, f"reporte_ventas_{fecha_hoy}.txt")
    csv_path = os.path.join(CARPETA_HISTORIAL, f"reporte_ventas_{fecha_hoy}.csv")

    with open(txt_path, "w", encoding="utf-8") as file:
        file.write(f"=== REPORTE DIARIO DE VENTAS - {fecha_hoy} ===\n\n")
        total_dia = 0
        for v in ventas_hoy:
            estado_str = f" [{v['estado']}]" if v['estado'] == 'ANULADA' else ""
            file.write(f"Venta #{v['id']} | Hora: {v['fecha_hora']} | Pago: {v['metodo_pago']} | Total: S/ {v['total']:.2f}{estado_str}\n")
            if v['estado'] != 'ANULADA':
                total_dia += v['total']
        file.write(f"\n=========================================\n")
        file.write(f"TOTAL RECAUDADO (VENTAS EFECTIVAS): S/ {total_dia:.2f}\n")

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["ID Venta", "Fecha y Hora", "Método de Pago", "Total (S/)", "Estado"])
        for v in ventas_hoy:
            writer.writerow([v['id'], v['fecha_hora'], v['metodo_pago'], f"{v['total']:.2f}", v['estado']])

    conn.close()
    
    func = request.environ.get('werkzeug.server.shutdown')
    if func is not None:
        func()
    else:
        os._exit(0)

    return jsonify({'success': True, 'message': 'Servidor cerrado correctamente'})

@app.route('/api/historial/vaciar', methods=['POST'])
def vaciar_historial():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM detalle_ventas")
        cursor.execute("DELETE FROM ventas")
        cursor.execute("DELETE FROM sqlite_sequence WHERE name IN ('ventas', 'detalle_ventas')")
        
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'Historial vaciado correctamente'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

def run_flask():
    app.run(host='127.0.0.1', port=5000)

if __name__ == '__main__':
    # Inicia Flask en un hilo secundario
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

    # Crea y muestra la ventana de escritorio nativa
    webview.create_window(
        'Sistema POS Sci-Fi', 
        'http://127.0.0.1:5000/', 
        width=1280, 
        height=800,
        resizable=True
    )
    webview.start()