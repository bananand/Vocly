"""
VOCLY CLIENT (Fixed Connection)
======================================================
Client menggunakan AsyncIO untuk komunikasi dengan server,
Flask-SocketIO untuk komunikasi dengan browser.

reference for AsyncIO: AsyncIOClient.py from https://untarid.sharepoint.com/sites/GJ2526-TK33010-DistributedSystemsC-LelyHiryanto/Class%20Materials/Forms/AllItems.aspx?id=%2Fsites%2FGJ2526%2DTK33010%2DDistributedSystemsC%2DLelyHiryanto%2FClass%20Materials%2FPraktikum%2FNetCodes&p=true&ga=1
"""

import asyncio
import json
import argparse
import threading
import time
from flask import Flask, render_template
from flask_socketio import SocketIO

# ============================================================
# FLASK SETUP
# ============================================================

app = Flask(__name__)
app.config['SECRET_KEY'] = 'vocly-client-secret-2024'
flask_socket = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ============================================================
# GLOBAL STATE
# ============================================================

server_reader = None
server_writer = None
client_id = None
connected_to_server = False
asyncio_loop = None
connection_ready = threading.Event()  # Signal saat connection siap 

# ============================================================
# ASYNCIO COMMUNICATION
# ============================================================

async def send_to_server(command, data=None):
    """Kirim command ke server"""
    global server_writer
    
    if not server_writer:
        print(f"[ERROR] Not connected to server (command: {command})")
        return False
    
    try:
        message = {
            'command': command,
            'data': data or {}
        }
        
        json_str = json.dumps(message) + '\n'
        server_writer.write(json_str.encode())
        await server_writer.drain()
        print(f"[→ SERVER] {command}")
        return True
    except Exception as e:
        print(f"[ERROR] Send to server: {e}")
        return False

async def read_from_server():
    """Baca messages dari server dan forward ke browser"""
    global server_reader, flask_socket
    
    print("[SERVER READ] Starting...")
    
    try:
        while True:
            line_bytes = await server_reader.readline()
            
            if not line_bytes:
                print("[SERVER] Connection closed")
                break
            
            try:
                line = line_bytes.decode().strip()
                message = json.loads(line)
                
                msg_type = message.get('type')
                data = message.get('data', {})
                
                print(f"[← SERVER] {msg_type}")
                
                # Forward ke browser via Flask-SocketIO
                flask_socket.emit(msg_type, data)
                
            except json.JSONDecodeError as e:
                print(f"[ERROR] Invalid JSON: {e}")
            except Exception as e:
                print(f"[ERROR] Processing message: {e}")
    
    except asyncio.CancelledError:
        print("[SERVER READ] Cancelled")
    except Exception as e:
        print(f"[ERROR] Read from server: {e}")

async def connect_to_server(server_host, server_port, max_retries=5):
    """Connect ke server dengan retry mechanism"""
    global server_reader, server_writer, client_id, connected_to_server
    
    for attempt in range(max_retries):
        try:
            print(f"[CONNECTING] Attempt {attempt + 1}/{max_retries} to {server_host}:{server_port}...")
            
            # Try to connect
            server_reader, server_writer = await asyncio.open_connection(
                server_host, server_port
            )
            
            print("[CONNECTED] Successfully connected to server")
            
            # Read welcome message with timeout
            try:
                welcome_line = await asyncio.wait_for(
                    server_reader.readline(),
                    timeout=10.0
                )
                
                if not welcome_line:
                    print("[ERROR] Empty welcome message")
                    raise Exception("No welcome message received")
                
                welcome = json.loads(welcome_line.decode().strip())
                
                if welcome.get('type') == 'welcome':
                    client_id = welcome['data']['client_id']
                    connected_to_server = True
                    print(f"[REGISTERED] Client ID: {client_id}")
                    
                    # Notify browser
                    flask_socket.emit('client_info', {
                        'client_id': client_id,
                        'server_connected': True
                    })
                    
                    # Signal that connection is ready
                    connection_ready.set()
                    
                    return True
                else:
                    print(f"[ERROR] Unexpected welcome type: {welcome.get('type')}")
                    raise Exception("Unexpected welcome message")
                    
            except asyncio.TimeoutError:
                print("[ERROR] Timeout waiting for welcome message")
                raise Exception("Welcome timeout")
                
        except Exception as e:
            print(f"[ERROR] Connection attempt {attempt + 1} failed: {e}")
            
            # Close writer if exists
            if server_writer:
                server_writer.close()
                try:
                    await server_writer.wait_closed()
                except:
                    pass
                server_writer = None
            
            # Wait before retry
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff: 1, 2, 4, 8, 16 seconds
                print(f"[RETRY] Waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)
    
    print("[ERROR] All connection attempts failed")
    connected_to_server = False
    return False

async def run_async_tasks(server_host, server_port):
    """Run all async tasks"""
    success = await connect_to_server(server_host, server_port)
    
    if not success:
        print("[FATAL] Could not connect to server")
        connection_ready.set()  # Signal even on failure so main thread doesn't hang
        return False
    
    # Start reading from server
    await read_from_server()
    
    return True

# ============================================================
# THREAD-SAFE COMMAND EXECUTION
# ============================================================

def run_async_command(coro):
    """Execute async command from Flask thread"""
    global asyncio_loop
    
    if not asyncio_loop or not connected_to_server:
        print("[ERROR] AsyncIO loop not running or not connected")
        flask_socket.emit('error', {'message': 'Not connected to server'})
        return
    
    # Submit coroutine to asyncio loop safely
    future = asyncio.run_coroutine_threadsafe(coro, asyncio_loop)
    
    try:
        # Wait for result with timeout
        result = future.result(timeout=5.0)
        return result
    except TimeoutError:
        print("[ERROR] Command timeout")
        flask_socket.emit('error', {'message': 'Command timeout'})
    except Exception as e:
        print(f"[ERROR] Command execution: {e}")
        flask_socket.emit('error', {'message': str(e)})

# ============================================================
# FLASK ROUTES
# ============================================================

@app.route('/')
def index():
    """Serve UI"""
    return render_template('index.html')

# ============================================================
# FLASK-SOCKETIO HANDLERS
# ============================================================

@flask_socket.on('connect')
def handle_browser_connect():
    """Browser connected"""
    print("[BROWSER] Connected")
    flask_socket.emit('client_info', {
        'client_id': client_id,
        'server_connected': connected_to_server
    })

@flask_socket.on('create_room')
def handle_create_room():
    """Forward create room"""
    print("[BROWSER] create_room")
    run_async_command(send_to_server('create_room'))

@flask_socket.on('join_room')
def handle_join_room(data):
    """Forward join room"""
    print(f"[BROWSER] join_room: {data.get('room_code')}")
    run_async_command(send_to_server('join_room', {'room_code': data.get('room_code')}))

@flask_socket.on('start_game')
def handle_start_game():
    """Forward start game"""
    print("[BROWSER] start_game")
    run_async_command(send_to_server('start_game'))

@flask_socket.on('make_guess')
def handle_make_guess(data):
    """Forward guess"""
    guess = data.get('guess')
    print(f"[BROWSER] make_guess: {guess}")
    run_async_command(send_to_server('make_guess', {'guess': guess}))

@flask_socket.on('rematch')
def handle_rematch():
    """Forward rematch"""
    print("[BROWSER] rematch")
    run_async_command(send_to_server('rematch'))

# ============================================================
# ASYNCIO THREAD
# ============================================================

def run_asyncio_thread(server_host, server_port):
    """Run AsyncIO event loop in separate thread"""
    global asyncio_loop
    
    # Create new event loop for this thread
    asyncio_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(asyncio_loop)
    
    try:
        print("[ASYNC THREAD] Starting...")
        asyncio_loop.run_until_complete(run_async_tasks(server_host, server_port))
    except KeyboardInterrupt:
        print("\n[ASYNC THREAD] Interrupted")
    except Exception as e:
        print(f"[ASYNC THREAD ERROR] {e}")
    finally:
        # Cleanup
        global server_writer
        if server_writer:
            server_writer.close()
            try:
                asyncio_loop.run_until_complete(server_writer.wait_closed())
            except:
                pass
        asyncio_loop.close()
        print("[ASYNC THREAD] Stopped")

# ============================================================
# MAIN
# ============================================================

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="🎮 VOCLY CLIENT - Pure AsyncIO"
    )
    
    parser.add_argument('--server-host', type=str, default='localhost',
                        help='Server host (default: localhost)')
    parser.add_argument('--server-port', type=int, default=5000,
                        help='Server port (default: 5000)')
    parser.add_argument('--host', type=str, default='0.0.0.0',
                        help='Client host (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=8001,
                        help='Client port (default: 8001)')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print(f"Client UI: http://{args.host}:{args.port}")
    print(f"Server: {args.server_host}:{args.server_port}")
    print("=" * 60)
    print("")
    
    # Start AsyncIO thread
    async_thread = threading.Thread(
        target=run_asyncio_thread, 
        args=(args.server_host, args.server_port),
        daemon=True
    )
    async_thread.start()
    
    # Wait for connection to be established (with timeout)
    print("[MAIN] Waiting for server connection...")
    connection_established = connection_ready.wait(timeout=30.0)  # Wait max 30 seconds
    
    if not connection_established:
        print("=" * 60)
        print("Connection timeout after 30 seconds")
        print("=" * 60)
    
    if connected_to_server:
        print("=" * 60)
        print("Connected to server successfully!")
        print("=" * 60)
        print("Open browser:")
        print(f"http://localhost:{args.port}")
        print(f"http://{args.host}:{args.port}")
        print("=" * 60)
        print("")
        
        # Run Flask (blocking)
        try:
            flask_socket.run(
                app, 
                host=args.host, 
                port=args.port, 
                debug=False,
                use_reloader=False  # IMPORTANT: No reloader!
            )
        except KeyboardInterrupt:
            print("\n[FLASK] Stopped")
    else:
        print("=" * 60)
        print("Failed to connect to server!")
        print("=" * 60)
        print("Troubleshooting:")
        print(f"   1. Check if server is running:")
        print(f"      python server.py --host 0.0.0.0 --port {args.server_port}")
        print(f"")
        print(f"   2. Test server connection:")
        print(f"      telnet {args.server_host} {args.server_port}")
        print(f"")
        print(f"   3. Check firewall:")
        print(f"      sudo ufw status")
        print(f"      sudo ufw allow {args.server_port}/tcp")
        print(f"")
        print(f"   4. Verify server IP:")
        print(f"      ping {args.server_host}")
        print("=" * 60)
        
        # Exit instead of continuing
        import sys
        sys.exit(1)