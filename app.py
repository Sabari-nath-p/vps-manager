from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import paramiko
import json
import os
import stat
import time
import posixpath
from functools import wraps
from cryptography.fernet import Fernet
import base64
import hashlib

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Encryption key for storing passwords
def get_encryption_key():
    key_file = 'secret.key'
    if os.path.exists(key_file):
        with open(key_file, 'rb') as f:
            return f.read()
    key = Fernet.generate_key()
    with open(key_file, 'wb') as f:
        f.write(key)
    return key

def encrypt_password(password):
    f = Fernet(get_encryption_key())
    return f.encrypt(password.encode()).decode()

def decrypt_password(encrypted_password):
    f = Fernet(get_encryption_key())
    return f.decrypt(encrypted_password.encode()).decode()

SERVERS_FILE = 'servers.json'

def load_servers():
    if os.path.exists(SERVERS_FILE):
        with open(SERVERS_FILE, 'r') as f:
            return json.load(f)
    return []

def save_servers(servers):
    with open(SERVERS_FILE, 'w') as f:
        json.dump(servers, f, indent=2)

def get_ssh_client(server_id):
    servers = load_servers()
    server = next((s for s in servers if s['id'] == server_id), None)
    if not server:
        return None, "Server not found"
    
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        password = decrypt_password(server['password'])
        client.connect(
            hostname=server['ip'],
            port=server.get('port', 22),
            username=server['username'],
            password=password,
            timeout=10
        )
        return client, None
    except Exception as e:
        return None, str(e)

def ssh_exec(client, command):
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=30)
        out = stdout.read().decode('utf-8', errors='replace')
        err = stderr.read().decode('utf-8', errors='replace')
        exit_code = stdout.channel.recv_exit_status()
        return out, err, exit_code
    except Exception as e:
        return '', str(e), 1

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    servers = load_servers()
    # Remove passwords before sending to frontend
    safe_servers = [{k: v for k, v in s.items() if k != 'password'} for s in servers]
    return render_template('index.html', servers=safe_servers)

@app.route('/api/servers', methods=['GET'])
def get_servers():
    servers = load_servers()
    safe = [{k: v for k, v in s.items() if k != 'password'} for s in servers]
    return jsonify(safe)

@app.route('/api/servers', methods=['POST'])
def add_server():
    data = request.json
    servers = load_servers()
    new_server = {
        'id': str(int(time.time() * 1000)),
        'name': data['name'],
        'ip': data['ip'],
        'port': data.get('port', 22),
        'username': data['username'],
        'password': encrypt_password(data['password']),
        'color': data.get('color', '#00ff88')
    }
    servers.append(new_server)
    save_servers(servers)
    safe = {k: v for k, v in new_server.items() if k != 'password'}
    return jsonify(safe)

@app.route('/api/servers/<server_id>', methods=['DELETE'])
def delete_server(server_id):
    servers = load_servers()
    servers = [s for s in servers if s['id'] != server_id]
    save_servers(servers)
    return jsonify({'success': True})

@app.route('/api/servers/<server_id>/test', methods=['POST'])
def test_connection(server_id):
    client, error = get_ssh_client(server_id)
    if error:
        return jsonify({'success': False, 'error': error})
    client.close()
    return jsonify({'success': True})

@app.route('/api/servers/<server_id>/stats', methods=['GET'])
def get_stats(server_id):
    client, error = get_ssh_client(server_id)
    if error:
        return jsonify({'error': error}), 500

    stats = {}
    
    # CPU usage
    out, _, _ = ssh_exec(client, "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1")
    stats['cpu'] = out.strip() or '0'

    # Memory
    out, _, _ = ssh_exec(client, "free -m | awk 'NR==2{printf \"%.1f %.1f %.1f\", $3,$2,$3/$2*100}'")
    parts = out.strip().split()
    if len(parts) == 3:
        stats['mem_used'] = parts[0]
        stats['mem_total'] = parts[1]
        stats['mem_pct'] = parts[2]
    
    # Disk
    out, _, _ = ssh_exec(client, "df -h / | awk 'NR==2{print $3,$2,$5}'")
    parts = out.strip().split()
    if len(parts) == 3:
        stats['disk_used'] = parts[0]
        stats['disk_total'] = parts[1]
        stats['disk_pct'] = parts[2].replace('%','')

    # Uptime
    out, _, _ = ssh_exec(client, "uptime -p")
    stats['uptime'] = out.strip()

    # Load average
    out, _, _ = ssh_exec(client, "cat /proc/loadavg")
    parts = out.strip().split()
    if parts:
        stats['load'] = ' '.join(parts[:3])

    # OS info
    out, _, _ = ssh_exec(client, "cat /etc/os-release | grep PRETTY_NAME | cut -d'\"' -f2")
    stats['os'] = out.strip()

    client.close()
    return jsonify(stats)

@app.route('/api/servers/<server_id>/files', methods=['GET'])
def list_files(server_id):
    path = request.args.get('path', '/')
    client, error = get_ssh_client(server_id)
    if error:
        return jsonify({'error': error}), 500
    try:
        sftp = client.open_sftp()
        entries = sftp.listdir_attr(path)
    except Exception as e:
        client.close()
        return jsonify({'error': str(e)}), 400

    files = []
    for attr in entries:
        name = attr.filename
        if name in ('.', '..'):
            continue
        mode = attr.st_mode
        if stat.S_ISDIR(mode):
            ftype = 'dir'
        elif stat.S_ISLNK(mode):
            ftype = 'link'
        else:
            ftype = 'file'
        perms = stat.filemode(mode)
        size = attr.st_size
        date = time.strftime('%Y-%m-%d %H:%M', time.localtime(attr.st_mtime))
        full_path = posixpath.join(path.rstrip('/'), name) if path != '/' else '/' + name
        files.append({
            'name': name,
            'type': ftype,
            'size': size,
            'date': date,
            'perms': perms,
            'path': full_path
        })

    sftp.close()
    client.close()

    # Sort: dirs first
    files.sort(key=lambda x: (0 if x['type'] == 'dir' else 1, x['name'].lower()))
    return jsonify({'files': files, 'path': path})

@app.route('/api/servers/<server_id>/file', methods=['GET'])
def read_file(server_id):
    path = request.args.get('path', '')
    client, error = get_ssh_client(server_id)
    if error:
        return jsonify({'error': error}), 500
    
    # Check file size first
    out, _, _ = ssh_exec(client, f"stat -c%s {json.dumps(path)} 2>/dev/null")
    size = int(out.strip()) if out.strip().isdigit() else 0
    
    if size > 1024 * 1024:  # 1MB limit
        client.close()
        return jsonify({'error': 'File too large to display (>1MB)', 'size': size}), 400
    
    out, err, code = ssh_exec(client, f"cat {json.dumps(path)} 2>&1")
    client.close()
    
    if code != 0:
        return jsonify({'error': err}), 400
    
    return jsonify({'content': out, 'path': path})

@app.route('/api/servers/<server_id>/file', methods=['PUT'])
def write_file(server_id):
    data = request.json
    path = data['path']
    content = data['content']
    client, error = get_ssh_client(server_id)
    if error:
        return jsonify({'error': error}), 500
    
    # Write via stdin to handle special chars
    cmd = f"cat > {json.dumps(path)}"
    stdin, stdout, stderr = client.exec_command(cmd)
    stdin.write(content)
    stdin.channel.shutdown_write()
    exit_code = stdout.channel.recv_exit_status()
    client.close()
    
    if exit_code != 0:
        return jsonify({'error': 'Failed to write file'}), 400
    return jsonify({'success': True})

@app.route('/api/servers/<server_id>/terminal', methods=['POST'])
def run_command(server_id):
    data = request.json
    command = data.get('command', '')
    cwd = data.get('cwd', '/')
    
    if not command:
        return jsonify({'output': '', 'error': ''}), 200
    
    client, error = get_ssh_client(server_id)
    if error:
        return jsonify({'error': error}), 500
    
    full_cmd = f"cd {json.dumps(cwd)} && {command} 2>&1"
    out, err, code = ssh_exec(client, full_cmd)
    client.close()
    
    return jsonify({'output': out, 'error': err, 'exit_code': code})

@app.route('/api/servers/<server_id>/pm2', methods=['GET'])
def pm2_list(server_id):
    client, error = get_ssh_client(server_id)
    if error:
        return jsonify({'error': error}), 500
    out, err, code = ssh_exec(client, "pm2 jlist 2>/dev/null || echo '[]'")
    client.close()
    try:
        processes = json.loads(out.strip())
        return jsonify({'processes': processes})
    except:
        return jsonify({'processes': [], 'raw': out})

@app.route('/api/servers/<server_id>/pm2/<action>/<pm2_id>', methods=['POST'])
def pm2_action(server_id, action, pm2_id):
    if action not in ['start', 'stop', 'restart', 'delete', 'reload']:
        return jsonify({'error': 'Invalid action'}), 400
    client, error = get_ssh_client(server_id)
    if error:
        return jsonify({'error': error}), 500
    out, err, code = ssh_exec(client, f"pm2 {action} {pm2_id}")
    client.close()
    return jsonify({'output': out, 'error': err, 'success': code == 0})

@app.route('/api/servers/<server_id>/pm2/deploy', methods=['POST'])
def pm2_deploy(server_id):
    data = request.json
    name = data.get('name')
    path = data.get('path')
    app_type = data.get('type', 'node')  # node, next, react, nest
    port = data.get('port', 3000)
    
    client, error = get_ssh_client(server_id)
    if error:
        return jsonify({'error': error}), 500
    
    if app_type == 'next':
        start_cmd = f"cd {path} && pm2 start npm --name {json.dumps(name)} -- start"
    elif app_type == 'react':
        start_cmd = f"cd {path} && npx serve -s build -l {port} & pm2 start {port} --name {json.dumps(name)}"
    elif app_type == 'nest':
        start_cmd = f"cd {path} && pm2 start dist/main.js --name {json.dumps(name)}"
    else:
        start_cmd = f"cd {path} && pm2 start npm --name {json.dumps(name)} -- start"
    
    out, err, code = ssh_exec(client, start_cmd)
    client.close()
    return jsonify({'output': out, 'error': err, 'success': code == 0})

@app.route('/api/servers/<server_id>/nginx', methods=['GET'])
def nginx_sites(server_id):
    client, error = get_ssh_client(server_id)
    if error:
        return jsonify({'error': error}), 500
    cmd = "if [ -d /etc/nginx/sites-available ]; then echo /etc/nginx/sites-available; ls /etc/nginx/sites-available; elif [ -d /etc/nginx/conf.d ]; then echo /etc/nginx/conf.d; ls /etc/nginx/conf.d; fi"
    out, _, _ = ssh_exec(client, cmd)
    client.close()
    lines = [s.strip() for s in out.strip().split('\n') if s.strip()]
    base_dir = lines[0] if lines else ''
    sites = lines[1:] if len(lines) > 1 else []
    return jsonify({'sites': sites, 'base_dir': base_dir})

@app.route('/api/servers/<server_id>/project/detect', methods=['POST'])
def detect_project(server_id):
    data = request.json or {}
    path = data.get('path', '/')
    client, error = get_ssh_client(server_id)
    if error:
        return jsonify({'error': error}), 500

    cmd = (
        f"cd {json.dumps(path)} && "
        "if [ -f package.json ]; then "
        "  if grep -q '\"next\"' package.json; then echo TYPE=next; "
        "  elif grep -q '\"@nestjs' package.json; then echo TYPE=nest; "
        "  elif grep -q '\"react-scripts\"' package.json; then echo TYPE=react; "
        "  else echo TYPE=node; fi; "
        "else echo TYPE=unknown; fi; "
        "if [ -d .git ]; then echo GIT=1; else echo GIT=0; fi; "
        "if [ -d .git ]; then git rev-parse --abbrev-ref HEAD 2>/dev/null | awk '{print \"BRANCH=\"$0}'; fi; "
        "if [ -d .git ]; then git branch --format=\"%(refname:short)\" 2>/dev/null | sed 's/^/BRANCH_LIST=/'; fi"
    )

    out, err, code = ssh_exec(client, cmd)
    client.close()

    if code != 0:
        return jsonify({'error': err or out or 'Detect failed'}), 400

    result = {'type': 'unknown', 'has_git': False, 'branch': '', 'branches': []}
    for line in out.strip().split('\n'):
        if line.startswith('TYPE='):
            result['type'] = line.split('=', 1)[1].strip()
        elif line.startswith('GIT='):
            result['has_git'] = line.split('=', 1)[1].strip() == '1'
        elif line.startswith('BRANCH='):
            result['branch'] = line.split('=', 1)[1].strip()
        elif line.startswith('BRANCH_LIST='):
            result['branches'].append(line.split('=', 1)[1].strip())

    return jsonify(result)

@app.route('/api/servers/<server_id>/nginx/create', methods=['POST'])
def nginx_create(server_id):
    data = request.json
    domain = data.get('domain')
    port = data.get('port', 3000)
    
    config = f"""server {{
    listen 80;
    server_name {domain} www.{domain};

    location / {{
        proxy_pass http://localhost:{port};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }}
}}"""
    
    client, error = get_ssh_client(server_id)
    if error:
        return jsonify({'error': error}), 500
    
    cmd = f"echo {json.dumps(config)} | sudo tee /etc/nginx/sites-available/{domain} && sudo ln -sf /etc/nginx/sites-available/{domain} /etc/nginx/sites-enabled/{domain} && sudo nginx -t && sudo systemctl reload nginx"
    out, err, code = ssh_exec(client, cmd)
    client.close()
    return jsonify({'output': out, 'error': err, 'success': code == 0, 'config': config})

@app.route('/api/servers/<server_id>/certbot', methods=['POST'])
def certbot_issue(server_id):
    data = request.json
    domain = data.get('domain')
    email = data.get('email')
    
    client, error = get_ssh_client(server_id)
    if error:
        return jsonify({'error': error}), 500
    
    cmd = f"sudo certbot --nginx -d {domain} -d www.{domain} --non-interactive --agree-tos -m {email} 2>&1"
    out, err, code = ssh_exec(client, cmd)
    client.close()
    return jsonify({'output': out, 'error': err, 'success': code == 0})

@app.route('/api/servers/<server_id>/logs/<pm2_id>', methods=['GET'])
def pm2_logs(server_id, pm2_id):
    lines = request.args.get('lines', 50)
    client, error = get_ssh_client(server_id)
    if error:
        return jsonify({'error': error}), 500
    out, err, _ = ssh_exec(client, f"pm2 logs {pm2_id} --lines {lines} --nostream 2>&1")
    client.close()
    return jsonify({'logs': out})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
