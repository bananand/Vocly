"""
VOCLY SERVER - Pure AsyncIO Backend
====================================
Server backend menggunakan pure AsyncIO streams.
Komunikasi via TCP streams dengan JSON protocol.

Arsitektur:
- AsyncIO server handle multiple clients
- Setiap client punya reader/writer stream
- Messages di-encode sebagai JSON

reference for AsyncIO: AsyncIOServer.py from https://untarid.sharepoint.com/sites/GJ2526-TK33010-DistributedSystemsC-LelyHiryanto/Class%20Materials/Forms/AllItems.aspx?id=%2Fsites%2FGJ2526%2DTK33010%2DDistributedSystemsC%2DLelyHiryanto%2FClass%20Materials%2FPraktikum%2FNetCodes&p=true&ga=1
"""

import asyncio
import random
import time
import json
import argparse
from datetime import datetime

# ============================================================
# KONSTANTA GAME
# ============================================================

WORD_BANK = [
    "REACT", "FLASK", "PIANO", "TIGER", "STORM", "BEACH", "TOWER", "SMOKE",
    "PLANT", "CLOUD", "MAGIC", "QUEST", "BRAVE", "CRISP", "FROST", "GLOBE",
    "DREAM", "FIGHT", "GRACE", "HEART", "JOKER", "KNIFE", "LUNAR", "MOUNT",
    "NIGHT", "OCEAN", "PRIDE", "QUICK", "RIVER", "SHINE", "TRUST", "UNITY",
    "VALOR", "WHALE", "YOUTH", "ZEBRA", "APPLE", "BREAD", "CHAIR", "DANCE",
    "EAGLE", "FLAME", "GRAPE", "HOUSE", "IMAGE", "JUICE", "LEMON", "MOUSE",
    "NURSE", "PARTY"
]

MAX_GUESSES = 6
WORD_LENGTH = 5
GAME_DURATION = 180

# ============================================================
# GLOBAL STATE
# ============================================================

# Menyimpan semua client yang terhubung
# Format: {client_id: (reader, writer, room_code)}
ALL_CLIENTS = {}

# Menyimpan semua room game
game_rooms = {}

# Timer tasks
active_timers = {}

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def generate_room_code():
    """Generate kode room unik"""
    return ''.join(random.choices('ABCDEFGHJKLMNPQRSTUVWXYZ23456789', k=5))

def generate_client_id():
    """Generate unique client ID"""
    return f"client_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"

def check_wordle_guess(guess, secret):
    """
    Logika Wordle: check guess vs secret word
    Returns: ['correct', 'present', 'absent', ...]
    """
    feedback = ['absent'] * len(secret)
    secret_counts = {}
    
    for char in secret:
        secret_counts[char] = secret_counts.get(char, 0) + 1
    
    # Pass 1: Correct positions
    for i in range(len(secret)):
        if guess[i] == secret[i]:
            feedback[i] = 'correct'
            secret_counts[guess[i]] -= 1
    
    # Pass 2: Present but wrong position
    for i in range(len(secret)):
        if feedback[i] == 'correct':
            continue
        if guess[i] in secret and secret_counts.get(guess[i], 0) > 0:
            feedback[i] = 'present'
            secret_counts[guess[i]] -= 1
    
    return feedback

def get_player_room(client_id):
    """Cari room dari client_id"""
    for room_code, room in game_rooms.items():
        if client_id in room['players']:
            return room_code, room
    return None, None

def stop_timer(room_code):
    """Stop timer untuk room"""
    if room_code in active_timers:
        task = active_timers[room_code]
        task.cancel()
        del active_timers[room_code]
        print(f"[TIMER STOP] Room {room_code}")

async def switch_command(client_id, writer, command, data):
    # Router untuk command client
    if command == 'create_room':
        await handle_create_room(client_id, writer)
    elif command == 'join_room':
        room_code = data.get('room_code')
        await handle_join_room(client_id, writer, room_code)
    elif command == 'start_game':
        await handle_start_game(client_id, writer)
    elif command == 'make_guess':
        guess = data.get('guess')
        await handle_make_guess(client_id, writer, guess)
    elif command == 'rematch':
        await handle_rematch(client_id, writer)
    elif command == 'QUIT':
        return True
    else:
        await send_message(writer, 'error', {
            'message': f'Unknown command: {command}'
        })
    return False

# ============================================================
# MESSAGE HANDLING
# ============================================================

async def send_message(writer, message_type, data):
    """
    Kirim message ke client dalam format JSON.
    Format: {"type": "...", "data": {...}}\n
    """
    message = {
        'type': message_type,
        'data': data,
        'timestamp': time.time()
    }
    json_str = json.dumps(message) + '\n'
    writer.write(json_str.encode())
    await writer.drain()

async def broadcast_to_room(room_code, message_type, data, exclude_client=None):
    """Broadcast message ke semua client di room"""
    room = game_rooms.get(room_code)
    if not room:
        return
    
    for client_id in room['players']:
        if client_id == exclude_client:
            continue
        if client_id in ALL_CLIENTS:
            _, writer, _ = ALL_CLIENTS[client_id]
            await send_message(writer, message_type, data)

# ============================================================
# GAME LOGIC
# ============================================================

async def check_game_end(room_code):
    """Check apakah kedua player sudah selesai dan tentukan pemenang"""
    room = game_rooms.get(room_code)
    if not room or room['state'] != 'playing':
        return
    
    all_finished = all(
        room['finish_times'][pid] is not None 
        for pid in room['players']
    )
    
    if not all_finished:
        return
    
    room['state'] = 'finished'
    stop_timer(room_code)
    
    p1 = room['players'][0]
    p2 = room['players'][1]
    
    t1 = room['finish_times'][p1]
    t2 = room['finish_times'][p2]
    
    r1 = room['results'][p1]
    r2 = room['results'][p2]
    
    # Determine winner
    if r1 == 'lost' and r2 == 'lost':
        result = f"SERI! Kedua pemain kehabisan kesempatan. Kata: {room['word']}"
    elif r1 == 'won' and r2 == 'lost':
        result = f"Pemain 1 MENANG! ({t1:.2f}s)"
    elif r1 == 'lost' and r2 == 'won':
        result = f"Pemain 2 MENANG! ({t2:.2f}s)"
    else:
        if t1 < t2:
            result = f"Pemain 1 MENANG! ({t1:.2f}s vs {t2:.2f}s)"
        elif t2 < t1:
            result = f"Pemain 2 MENANG! ({t2:.2f}s vs {t1:.2f}s)"
        else:
            result = f"SERI! ({t1:.2f}s)"
    
    # Broadcast ke semua client di room
    await broadcast_to_room(room_code, 'game_over', {
        'result': result,
        'word': room['word']
    })
    
    print(f"[GAME END] Room {room_code}: {result}")

async def timer_game(room_code):
    """Background timer task"""
    print(f"[TIMER START] Room {room_code}")
    
    try:
        await asyncio.sleep(GAME_DURATION)  #Ini untuk flowchart
        
        room = game_rooms.get(room_code)
        if not room or room['state'] != 'playing':
            return
        
        print(f"[TIMEOUT] Room {room_code}")
        
        for pid in room['players']:
            if room['finish_times'][pid] is None:
                room['finish_times'][pid] = time.time() - room['start_time']
                room['results'][pid] = 'lost'
        
        if room_code in active_timers:
            del active_timers[room_code]
        
        await check_game_end(room_code)
        
    except asyncio.CancelledError:  #Ini untuk flowchart
        print(f"[TIMER CANCELLED] Room {room_code}")

# ============================================================
# COMMAND HANDLERS
# ============================================================

async def handle_create_room(client_id, writer):
    """Handle create room request"""
    room_code = generate_room_code()
    
    game_rooms[room_code] = {
        'players': [client_id],
        'word': random.choice(WORD_BANK).upper(),
        'state': 'waiting',
        'start_time': None,
        'guesses': {client_id: []},
        'finish_times': {client_id: None},
        'results': {client_id: None}
    }
    
    # Update client's room
    reader, _, _ = ALL_CLIENTS[client_id]
    ALL_CLIENTS[client_id] = (reader, writer, room_code)
    
    print(f"[CREATE] Room {room_code} by {client_id}")
    print(f"[SECRET] Word: {game_rooms[room_code]['word']}")
    
    await send_message(writer, 'room_created', {'room_code': room_code})

async def handle_join_room(client_id, writer, room_code):
    """Handle join room request"""
    room_code = room_code.upper().strip()
    
    if room_code not in game_rooms:
        await send_message(writer, 'error', {'message': 'Room tidak ditemukan'})
        return
    
    room = game_rooms[room_code]
    
    if len(room['players']) >= 2:
        await send_message(writer, 'error', {'message': 'Room penuh'})
        return
    
    if room['state'] != 'waiting':
        await send_message(writer, 'error', {'message': 'Game sudah dimulai'})
        return
    
    room['players'].append(client_id)
    room['guesses'][client_id] = []
    room['finish_times'][client_id] = None
    room['results'][client_id] = None
    room['state'] = 'ready'
    
    # Update client's room
    reader, _, _ = ALL_CLIENTS[client_id]
    ALL_CLIENTS[client_id] = (reader, writer, room_code)
    
    print(f"[JOIN] {client_id} joined {room_code}")
    
    # Broadcast ke semua client di room
    await broadcast_to_room(room_code, 'room_ready', {
        'message': 'Kedua pemain terhubung! Tekan START.',
        'player_count': 2
    })

async def handle_start_game(client_id, writer):
    """Handle start game request"""
    room_code, room = get_player_room(client_id)
    
    if not room:
        await send_message(writer, 'error', {'message': 'Room tidak ditemukan'})
        return
    
    if room['state'] != 'ready':
        await send_message(writer, 'error', {'message': 'Game tidak bisa dimulai'})
        return
    
    room['state'] = 'playing'
    room['start_time'] = time.time()
    
    # Reset state
    for pid in room['players']:
        room['finish_times'][pid] = None
        room['results'][pid] = None
        room['guesses'][pid] = []
    
    print(f"[START] Game dimulai di room {room_code}")
    
    # Broadcast ke semua client
    await broadcast_to_room(room_code, 'game_started', {
        'message': 'Game dimulai!',
        'word_length': WORD_LENGTH,
        'max_guesses': MAX_GUESSES,
        'duration': GAME_DURATION
    })
    
    # Start timer
    task = asyncio.create_task(timer_game(room_code)) #Ini untuk flowchart
    active_timers[room_code] = task

async def handle_make_guess(client_id, writer, guess):
    """Handle make guess request"""
    guess = guess.upper().strip()
    room_code, room = get_player_room(client_id)
    
    if not room or room['state'] != 'playing':
        await send_message(writer, 'error', {'message': 'Game tidak aktif'})
        return
    
    if room['finish_times'][client_id] is not None:
        return
    
    if len(guess) != WORD_LENGTH:
        await send_message(writer, 'error', {'message': f'Tebakan harus {WORD_LENGTH} huruf'})
        return
    
    secret = room['word']
    feedback = check_wordle_guess(guess, secret)
    room['guesses'][client_id].append((guess, feedback))
    
    guess_count = len(room['guesses'][client_id])
    is_correct = (guess == secret)
    
    print(f"[GUESS] {client_id} -> {guess} | Correct: {is_correct}")
    
    # Send ke player yang tebak
    await send_message(writer, 'guess_result', {
        'guess': guess,
        'feedback': feedback,
        'guess_count': guess_count,
        'is_correct': is_correct
    })
    
    # Broadcast ke opponent
    await broadcast_to_room(room_code, 'opponent_guessed', {
        'guess_count': guess_count
    }, exclude_client=client_id)
    
    # Check win/lose
    if is_correct:
        elapsed = time.time() - room['start_time']
        room['finish_times'][client_id] = elapsed
        room['results'][client_id] = 'won'
        
        await send_message(writer, 'player_finished', {
            'result': 'won',
            'time': elapsed,
            'message': 'Benar! Menunggu lawan...'
        })
        
        print(f"[WIN] {client_id} dalam {elapsed:.2f}s")
        await check_game_end(room_code)
    
    elif guess_count >= MAX_GUESSES:
        elapsed = time.time() - room['start_time']
        room['finish_times'][client_id] = elapsed
        room['results'][client_id] = 'lost'
        
        await send_message(writer, 'player_finished', {
            'result': 'lost',
            'time': elapsed,
            'message': f'Kesempatan habis! Kata: {secret}'
        })
        
        print(f"[LOSE] {client_id} setelah {MAX_GUESSES} tebakan")
        await check_game_end(room_code)

async def handle_rematch(client_id, writer):
    """Handle rematch request"""
    room_code, room = get_player_room(client_id)
    
    if not room or room['state'] != 'finished':
        await send_message(writer, 'error', {'message': 'Tidak bisa rematch'})
        return
    
    stop_timer(room_code)
    
    players = room['players']
    new_word = random.choice(WORD_BANK).upper()
    
    game_rooms[room_code] = {
        'players': players,
        'word': new_word,
        'state': 'ready',
        'start_time': None,
        'guesses': {p: [] for p in players},
        'finish_times': {p: None for p in players},
        'results': {p: None for p in players}
    }
    
    print(f"[REMATCH] Room {room_code} reset. Kata baru: {new_word}")
    
    await broadcast_to_room(room_code, 'rematch_ready', {
        'message': 'Rematch siap! Tekan START.'
    })


# ============================================================
# CLIENT HANDLER
# ============================================================

async def cleanup_client(client_id, writer):
    # Log disconnect client
    print(f"[DISCONNECT] Client {client_id}")
    
    # Cleanup room if in game
    room_code, room = get_player_room(client_id)
    if room_code and room:
        stop_timer(room_code)
        
        # Notify oppenent
        if room['state'] in ['ready', 'playing']:
            await broadcast_to_room(room_code, 'opponent_disconnected', {
                'message': 'Lawan terputus! Kamu MENANG!'
            }, exclude_client=client_id)
        
        # Delete room
        if room_code in game_rooms:
             del game_rooms[room_code]
    
    # Remove from clients
    if client_id in ALL_CLIENTS:
        del ALL_CLIENTS[client_id]
    
     # Close connection
    writer.close()
    await writer.wait_closed()

async def handle_client(reader, writer):
    """
    Handle setiap client connection.
    """
    addr = writer.get_extra_info('peername')
    print(f"[CONNECT] Client connecting from {addr}")
    
    # Generate client ID
    client_id = generate_client_id()
    
    # Welcome message
    await send_message(writer, 'welcome', {
        'message': 'Vocly Server - AsyncIO',
        'client_id': client_id,
        'server_info': {
            'word_length': WORD_LENGTH,
            'max_guesses': MAX_GUESSES,
            'game_duration': GAME_DURATION
        }
    })
    
    # Store client
    ALL_CLIENTS[client_id] = (reader, writer, None)
    print(f"[REGISTER] Client {client_id} dari {addr}")
    
    try:
        # Read messages from client
        while True:
            # Read line (mirip dengan referensi)
            line_bytes = await reader.readline()
            
            if not line_bytes:
                break
            
            # Parse JSON message
            try:
                line = line_bytes.decode().strip()
                message = json.loads(line)
                command = message.get('command')
                data = message.get('data', {})
                
                print(f"[CMD] {client_id} -> {command}")
                
                should_quit = await switch_command(client_id, writer, command, data)
                if should_quit:
                    break
            
            except json.JSONDecodeError:
                await send_message(writer, 'error', {
                    'message': 'Invalid JSON format'
                })
            except Exception as e:
                print(f"[ERROR] {e}")
                await send_message(writer, 'error', {
                    'message': str(e)
                })
    
    finally:
        await cleanup_client(client_id, writer)


# ============================================================
# MAIN SERVER
# ============================================================

async def main(host, port):
    """
    Main server function.
    Mirip dengan main() di referensi.
    """
    # Create server
    server = await asyncio.start_server(handle_client, host, port)  #Ini untuk flowchart
    
    addr = server.sockets[0].getsockname()
    print(f"[SERVING] on {addr}")
    
    # Run server
    async with server:
        await server.serve_forever()

# ============================================================
# RUN
# ============================================================

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description=" VOCLY SERVER - AsyncIO"
    )
    
    parser.add_argument('--host', type=str, default='0.0.0.0',
                        help='Host address (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=5000,
                        help='Port number (default: 5000)')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print(f"Word Bank: {len(WORD_BANK)} words")
    print(f"Duration: {GAME_DURATION}s")
    print(f"Max Guesses: {MAX_GUESSES}")
    print(f"Server: {args.host}:{args.port}")
    print("=" * 60)
    print("")
    
    try:
        asyncio.run(main(args.host, args.port))  #Ini untuk flowchart
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Server stopped by user")