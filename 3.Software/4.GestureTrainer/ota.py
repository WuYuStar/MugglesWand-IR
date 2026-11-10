"""Upload a generated application through the firmware's ElegantOTA 3.1 API."""
import hashlib
import http.client
import ipaddress
import secrets
import socket
import time


def device_address(host):
    try:
        address = ipaddress.IPv4Address(host)
    except (ValueError, TypeError):
        raise ValueError('请填写魔杖的局域网 IPv4 地址，默认 192.168.4.1。') from None
    if not address.is_private or address.is_loopback or address.is_unspecified or address.is_multicast:
        raise ValueError('无线更新只支持魔杖的局域网地址。')
    return str(address)


def application_bytes(path):
    data = path.read_bytes()
    if not 24 <= len(data) <= 0x1e0000 or data[0] != 0xe9 or data[12:14] != b'\x09\x00':
        raise ValueError('升级文件不是适用的 ESP32-S3 应用固件，请重新生成。')
    return data


def install(host, data, progress, *, http_port=80, tcp_port=8080, wait_seconds=45):
    # Direct HTTP connections intentionally bypass system internet proxies.
    def request(path):
        connection = http.client.HTTPConnection(host, http_port, timeout=3)
        try:
            connection.request('GET', path)
            response = connection.getresponse()
            body = response.read(65536)
            if response.status != 200:
                raise OSError(f'魔杖升级接口返回 HTTP {response.status}。')
            return body
        finally:
            connection.close()

    progress('connecting', 0, '正在连接魔杖，请保持电脑连接魔杖 Wi-Fi。')
    try:
        request('/update')
    except (OSError, http.client.HTTPException):
        try:
            with socket.create_connection((host, tcp_port), timeout=4) as connection:
                connection.sendall(b'ota\n')
        except OSError:
            # It may already be switching modes; wait for its OTA HTTP server.
            pass
        progress('waiting', 0, '魔杖正在切换升级模式；Wi-Fi 可能短暂断开，请重新连接同一热点。')
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                request('/update')
                break
            except (OSError, http.client.HTTPException):
                if time.monotonic() >= deadline:
                    raise OSError('未连上魔杖升级页。请连接魔杖 Wi-Fi 后重试；也可先在魔杖网页进入固件升级。') from None
                time.sleep(1)

    digest = hashlib.md5(data).hexdigest()
    request('/ota/start?mode=firmware&hash=' + digest)
    boundary = 'WandTrainer' + secrets.token_hex(16)
    prefix = (f'--{boundary}\r\nContent-Disposition: form-data; name="firmware"; '
              'filename="MugglesWand-custom-app.bin"\r\n'
              'Content-Type: application/octet-stream\r\n\r\n').encode()
    suffix = f'\r\n--{boundary}--\r\n'.encode()
    connection = http.client.HTTPConnection(host, http_port, timeout=45)
    try:
        connection.putrequest('POST', '/ota/upload')
        connection.putheader('Content-Type', 'multipart/form-data; boundary=' + boundary)
        connection.putheader('Content-Length', str(len(prefix) + len(data) + len(suffix)))
        connection.endheaders()
        connection.send(prefix)
        for offset in range(0, len(data), 16384):
            chunk = data[offset:offset + 16384]
            connection.send(chunk)
            progress('uploading', min(99, (offset + len(chunk)) * 100 // len(data)),
                     '正在发送新模型固件，请勿断电或关闭训练程序。')
        connection.send(suffix)
        progress('verifying', 99, '发送完成，正在等待魔杖校验。')
        response = connection.getresponse()
        body = response.read(4096).strip()
        if response.status != 200 or body != b'OK':
            raise OSError('魔杖未确认升级成功：' + body.decode('utf-8', errors='replace')[:200])
    except (OSError, http.client.HTTPException) as exc:
        raise OSError('未收到升级成功确认，请勿直接断电。稍候重连魔杖查看状态，再决定是否重试。' + str(exc)) from exc
    finally:
        connection.close()
    progress('complete', 100, '魔杖已校验并接受升级，正在重启。请重新连接热点，用新动作测试效果。')
