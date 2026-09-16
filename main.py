#!/usr/bin/env python3
"""
txreader.py  —  TxReader (Web)
Phiên bản web của desktop app, deploy được lên Render / Railway / Fly.io.

Cài đặt:    pip install flask
Chạy local: python txreader.py
Deploy:      Start command = `python txreader.py`  (đọc PORT từ env)
             Hoặc dùng gunicorn: `gunicorn txreader:app`
"""

import os
import re
import unicodedata
from flask import Flask, request, jsonify, render_template_string


# ===================== PARSING LOGIC =====================

def clean_text(text):
    """Loại bỏ ký tự ẩn: zero-width space, BOM, non-breaking space."""
    return (text.replace('\u200b', '')
                .replace('\ufeff', '')
                .replace('\u00a0', ' ')
                .strip())


def normalize_name(name):
    """Xóa dấu tiếng Việt, chuyển thành chữ hoa để so khớp chính xác."""
    s = unicodedata.normalize('NFD', name)
    s = s.encode('ascii', 'ignore').decode('utf-8')
    return s.strip().upper()


def get_account_type(counterparty_name):
    """Phân loại tài khoản dựa vào tên người đối tác."""
    norm = normalize_name(counterparty_name)
    lao_1_plus = ["DINH NGOC DUC", "TRAN THI THUY", "TRINH VAN SON", "NGUYEN VAN LOI"]
    lao_plus   = ["NGUYEN VAN MINH", "NGUYEN MINH TIEN"]
    lao_1      = ["TRAN THI KA NHA", "CAI VAN THONG", "TRAN XUAN QUY"]
    if norm in lao_1_plus:
        return "Lào 1 Plus"
    if norm in lao_plus:
        return "Lào Plus"
    if norm in lao_1:
        return "Lào 1"
    return "Không xác định"


def detect_tx_type(anchor, fallback):
    """Xác định chiều giao dịch: incoming (nhận) / outgoing (chuyển)."""
    if not anchor:
        return fallback
    a = anchor.upper()
    if 'NHẬN TIỀN TỪ' in a or 'NHAN TIEN TU' in a:
        return 'incoming'
    if 'CHUYỂN TIỀN ĐẾN' in a or 'CHUYEN TIEN DEN' in a:
        return 'outgoing'
    return fallback


def find_first_nonempty(parts, start):
    for i in range(start, len(parts)):
        if parts[i]:
            return i
    return None


def parse_transaction_line(line):
    """Parse một dòng giao dịch, hỗ trợ cả 2 format (chuyển đi / nhận về)."""
    line = clean_text(line)
    if not line:
        return None

    parts = [clean_text(p) for p in line.split('\t')]
    if len(parts) < 7:
        return None

    sender_stk       = parts[0]
    sender_name_full = parts[1]
    sender_bank      = parts[2]
    date             = parts[3]

    if not re.match(r'^\d{2}/\d{2}/\d{4}$', date):
        return None

    if sender_name_full.startswith('VND-TGTT-'):
        sender_name = sender_name_full[len('VND-TGTT-'):]
    else:
        sender_name = sender_name_full

    # F1 (chuyển đi): cột 5 trống → có 4 tab trống trước counterparty
    # F2 (nhận về):  cột 5 chính là tên đối tác
    is_f1 = (parts[4] == '')
    fallback_type = 'outgoing' if is_f1 else 'incoming'

    if is_f1:
        cp_idx = find_first_nonempty(parts, 4)
        if cp_idx is None or cp_idx + 2 >= len(parts):
            return None
        counterparty_name  = parts[cp_idx]
        counterparty_stk   = parts[cp_idx + 1]
        counterparty_bank  = parts[cp_idx + 2]

        amt_idx = find_first_nonempty(parts, cp_idx + 3)
        if amt_idx is None:
            return None
        amount = parts[amt_idx]
        anchor = parts[amt_idx + 1] if amt_idx + 1 < len(parts) else ''
    else:
        counterparty_name = parts[4]
        counterparty_stk  = parts[5]
        counterparty_bank = parts[6]

        amt_idx = find_first_nonempty(parts, 7)
        if amt_idx is None:
            return None
        amount = parts[amt_idx]
        anchor = parts[amt_idx + 1] if amt_idx + 1 < len(parts) else ''

    return {
        'sender_stk':        sender_stk,
        'sender_name':       sender_name,
        'sender_bank':       sender_bank,
        'date':              date,
        'counterparty_name': counterparty_name,
        'counterparty_stk':  counterparty_stk,
        'counterparty_bank': counterparty_bank,
        'amount':            amount,
        'tx_type':           detect_tx_type(anchor, fallback_type),
    }


def format_transactions(input_text):
    """Trả về tuple (output_text, transaction_count) hoặc (None, 0) nếu lỗi."""
    lines = input_text.split('\n')
    transactions = []
    for line in lines:
        tx = parse_transaction_line(line)
        if tx:
            transactions.append(tx)

    if not transactions:
        return None, 0

    # Header lấy từ giao dịch đầu tiên (giả định input là 1 tài khoản duy nhất)
    first = transactions[0]
    account_type = get_account_type(first['counterparty_name'])

    out = [
        f"Tài khoản con bạc {account_type}",
        f"STK: {first['sender_stk']}",
        f"Chủ TK: {first['sender_name']}",
        f"NH: {first['sender_bank']}",
    ]

    for tx in transactions:
        action = "Chuyển tiền cho" if tx['tx_type'] == 'outgoing' else "Nhận tiền từ"
        out.append(f"Ngày GD: {tx['date']}")
        out.append(
            f"{action} {tx['counterparty_name']} "
            f"({tx['counterparty_stk']} - {tx['counterparty_bank']}) "
            f"với số tiền {tx['amount']}đ"
        )

    return "\n".join(out), len(transactions)


# ===================== HTML TEMPLATE =====================

HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>⚡ TxReader</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }

body {
  background: #0a0f1c;
  color: #e2e8f0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  min-height: 100vh;
  padding: 30px 20px;
}

.container { max-width: 980px; margin: 0 auto; }

.header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 8px; gap: 12px; flex-wrap: wrap;
}

h1 {
  font-family: "Menlo", "Consolas", "Courier New", monospace;
  font-size: 22px; color: #00d9ff; letter-spacing: 1px; font-weight: 700;
}

.version {
  background: #131a2e; color: #94a3b8;
  padding: 4px 10px; border-radius: 4px;
  font-family: "Menlo", monospace; font-size: 11px; white-space: nowrap;
}

.subtitle {
  color: #94a3b8;
  font-family: "Menlo", "Consolas", monospace;
  font-size: 11px; margin-bottom: 16px;
}

.accent-line {
  height: 2px;
  background: linear-gradient(90deg, #00d9ff, transparent);
  margin-bottom: 22px; border-radius: 1px;
}

.card {
  background: #131a2e; border: 1px solid #2d3748;
  border-radius: 6px; margin-bottom: 14px; overflow: hidden;
}

.card-header {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 18px 10px;
  font-family: "Menlo", "Consolas", monospace;
  font-size: 11px; font-weight: 700;
}

.label-input  { color: #00d9ff; }
.label-output { color: #10ff88; }
.card-header-sub { color: #94a3b8; font-weight: 400; }

textarea {
  display: block;
  width: calc(100% - 30px);
  margin: 0 15px 15px;
  background: #050912; color: #e2e8f0;
  border: none; border-radius: 4px;
  padding: 14px 15px;
  font-family: "Menlo", "Consolas", monospace;
  font-size: 12px; line-height: 1.5;
  outline: none; resize: vertical; min-height: 140px;
}

textarea::placeholder { color: #475569; }
textarea:focus { box-shadow: inset 0 0 0 1px #00d9ff; }
textarea[readonly] { cursor: default; }

.btn-row {
  display: flex; gap: 10px; margin: 14px 0; flex-wrap: wrap;
}

.btn {
  padding: 12px 24px; border: none; border-radius: 4px;
  font-family: inherit; font-weight: 700; font-size: 12px;
  cursor: pointer; white-space: nowrap;
  transition: transform 0.1s, background-color 0.2s, box-shadow 0.2s;
  letter-spacing: 0.5px;
}
.btn:active { transform: translateY(1px); }

.btn-primary  { background: #00d9ff; color: #0a0f1c; }
.btn-primary:hover {
  background: #00b8d4;
  box-shadow: 0 0 16px rgba(0, 217, 255, 0.3);
}

.btn-danger {
  background: transparent; color: #ff4d6d;
  border: 1px solid #2d3748;
}
.btn-danger:hover {
  background: rgba(255, 77, 109, 0.15);
  border-color: #ff4d6d;
}

.btn-success {
  background: #10ff88; color: #0a0f1c;
  margin-left: auto;
}
.btn-success:hover {
  background: #00e673;
  box-shadow: 0 0 16px rgba(16, 255, 136, 0.3);
}

.status-bar {
  display: flex; justify-content: space-between; align-items: center;
  color: #94a3b8;
  font-family: "Menlo", "Consolas", monospace;
  font-size: 10px; margin-top: 14px;
  flex-wrap: wrap; gap: 8px;
}
.status-bar .status { transition: color 0.3s; }

.toast {
  position: fixed; bottom: 30px; right: 30px;
  background: #131a2e; border: 1px solid #10ff88;
  color: #10ff88; padding: 12px 22px; border-radius: 4px;
  font-family: "Menlo", monospace; font-size: 12px;
  opacity: 0; transform: translateY(20px);
  transition: opacity 0.3s, transform 0.3s;
  pointer-events: none;
  box-shadow: 0 4px 24px rgba(16, 255, 136, 0.2);
  z-index: 1000;
}
.toast.show { opacity: 1; transform: translateY(0); }

@media (max-width: 640px) {
  body { padding: 16px 12px; }
  h1 { font-size: 18px; }
  .btn { padding: 10px 16px; font-size: 11px; }
  .btn-success { margin-left: 0; width: 100%; }
}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>⚡ TxReader</h1>
    <span class="version">v 2.0 · web</span>
  </div>
  <div class="subtitle">// Chuyển đổi giao dịch ngân hàng (chuyển đi / nhận về)</div>
  <div class="accent-line"></div>

  <div class="card">
    <div class="card-header">
      <span class="label-input">◉ INPUT</span>
      <span class="card-header-sub">Dán chuỗi giao dịch vào đây</span>
    </div>
    <textarea id="input" placeholder="Paste transaction data here..."></textarea>
  </div>

  <div class="btn-row">
    <button class="btn btn-primary" onclick="processInput()">⚡ CHUYỂN ĐỔI</button>
    <button class="btn btn-danger"  onclick="clearFields()">✕ XÓA</button>
    <button class="btn btn-success" onclick="copyOutput()">⧉ COPY</button>
  </div>

  <div class="card">
    <div class="card-header">
      <span class="label-output">◉ OUTPUT</span>
      <span class="card-header-sub">Kết quả đã format</span>
    </div>
    <textarea id="output" readonly placeholder="Output sẽ hiển thị ở đây..."></textarea>
  </div>

  <div class="status-bar">
    <span class="status" id="status">○ Sẵn sàng</span>
    <span>Flask · HTML · JS</span>
  </div>
</div>

<div class="toast" id="toast">⧉ Đã copy!</div>

<script>
  const $input  = document.getElementById('input');
  const $output = document.getElementById('output');
  const $status = document.getElementById('status');
  const $toast  = document.getElementById('toast');

  async function processInput() {
    const text = $input.value.trim();
    if (!text) {
      setStatus('⚠ Chưa có dữ liệu', '#ff4d6d');
      return;
    }
    setStatus('◌ Đang xử lý...', '#00d9ff');
    try {
      const res = await fetch('/api/format', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ text })
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        setStatus('✕ ' + (data.error || 'Lỗi không xác định'), '#ff4d6d');
        return;
      }
      $output.value = data.result;
      setStatus(`✓ Đã xử lý ${data.count} giao dịch`, '#10ff88');
    } catch (e) {
      setStatus('✕ Lỗi kết nối: ' + e.message, '#ff4d6d');
    }
  }

  function clearFields() {
    $input.value = '';
    $output.value = '';
    setStatus('○ Sẵn sàng', '#94a3b8');
    $input.focus();
  }

  async function copyOutput() {
    const out = $output.value;
    if (!out) {
      setStatus('⚠ Không có dữ liệu để copy', '#ff4d6d');
      return;
    }
    try {
      await navigator.clipboard.writeText(out);
    } catch {
      $output.select();
      document.execCommand('copy');
    }
    setStatus('⧉ Đã copy vào clipboard', '#10ff88');
    showToast('⧉ Đã copy!');
  }

  function setStatus(msg, color) {
    $status.textContent = msg;
    $status.style.color = color;
  }

  function showToast(msg) {
    $toast.textContent = msg;
    $toast.classList.add('show');
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => $toast.classList.remove('show'), 1800);
  }

  // Ctrl/Cmd + Enter để xử lý nhanh
  $input.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      processInput();
    }
  });
</script>
</body>
</html>
"""


# ===================== FLASK APP =====================

app = Flask(__name__)


@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/api/format', methods=['POST'])
def api_format():
    """Format transactions từ JSON body { text: '...' }."""
    data = request.get_json(silent=True) or {}
    text = data.get('text', '').strip()
    if not text:
        return jsonify({'success': False, 'error': 'Chưa có dữ liệu'}), 400

    result, count = format_transactions(text)
    if result is None:
        return jsonify({'success': False, 'error': 'Không parse được dòng nào hợp lệ'}), 400

    return jsonify({'success': True, 'result': result, 'count': count})


@app.route('/api/health')
def health():
    """Health check endpoint cho monitoring / deploy platform."""
    return jsonify({'status': 'ok'})


# ===================== MAIN =====================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'

    print('=' * 54)
    print('  ⚡ TxReader  —  Web')
    print(f'  Local:    http://localhost:{port}')
    print('  Deploy:   set PORT env, bind 0.0.0.0')
    print('  Health:   GET /api/health')
    print('=' * 54)

    app.run(host='0.0.0.0', port=port, debug=debug)
