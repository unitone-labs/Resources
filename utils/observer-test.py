import pika
import json
from hashlib import blake2b

# Connection details
url = "bittensor-observer.flamewire.io"
username = "test-owners"
password = "Hul3YQdO5WXU"
queue_name = "test-observer"

# Create credentials
credentials = pika.PlainCredentials(username, password)

# Connection parameters
parameters = pika.ConnectionParameters(
    host=url,
    port=5672,          # Default AMQP port
    virtual_host="/",   # change if a different vhost
    credentials=credentials
)

# Connect to RabbitMQ
connection = pika.BlockingConnection(parameters)
channel = connection.channel()

print(f"[✓] Connected. Listening for messages on queue '{queue_name}'...")

# Callback for each received message
def callback(ch, method, properties, body):
    try:
        data = json.loads(body.decode(errors='ignore'))
        evt = data.get('event_data', {}) if isinstance(data, dict) else {}

        def _to_bytes(arr):
            if isinstance(arr, list) and len(arr) == 1 and isinstance(arr[0], list):
                arr = arr[0]
            if isinstance(arr, list) and all(isinstance(i, int) for i in arr):
                try:
                    return bytes(arr)
                except Exception:
                    return None
            return None

        def _b58encode(b):
            alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
            n = int.from_bytes(b, 'big')
            res = ''
            while n > 0:
                n, rem = divmod(n, 58)
                res = alphabet[rem] + res
            # handle leading zeros
            pad = 0
            for byte in b:
                if byte == 0:
                    pad += 1
                else:
                    break
            return ('1' * pad) + res if res else ('1' * pad)

        def _ss58_encode(pubkey_bytes, ss58_format=42):
            if not isinstance(pubkey_bytes, (bytes, bytearray)) or len(pubkey_bytes) != 32:
                return None
            prefix = bytes([ss58_format])
            payload = prefix + bytes(pubkey_bytes)
            checksum = blake2b(b'SS58PRE' + payload, digest_size=64).digest()[:2]
            return _b58encode(payload + checksum)

        def _decode_value(value):
            # Recursively decode structures; return decoded value or original
            if isinstance(value, dict):
                return {k: _decode_value(v) for k, v in value.items()}
            if isinstance(value, list):
                # Try to decode as bytes first
                b = _to_bytes(value)
                if b is not None:
                    if len(b) == 32:
                        addr = _ss58_encode(b)
                        return {'_bytes': list(b), 'ss58': addr} if addr else {'_bytes': list(b)}
                    else:
                        return {'_bytes': list(b)}
                # Otherwise decode each element
                return [_decode_value(v) for v in value]
            # No change for primitives
            return value

        def _decode_extrinsic_failed(data):
            # Attempt to decode common shape: {dispatch_error: {name: "Module", values: [{error: [x,0,0,0], index: N}]}}
            try:
                de = data.get('dispatch_error')
                if isinstance(de, dict) and de.get('name') == 'Module':
                    vals = de.get('values')
                    if isinstance(vals, list) and vals:
                        v0 = vals[0]
                        err_arr = v0.get('error') if isinstance(v0, dict) else None
                        index = v0.get('index') if isinstance(v0, dict) else None
                        if isinstance(err_arr, list) and len(err_arr) >= 1 and isinstance(err_arr[0], int):
                            error_code = err_arr[0]
                            return {'module_index': index, 'error_code': error_code, 'error_hex': hex(error_code)}
            except Exception:
                pass
            return None

        # Build decoded view without mutating original event_data
        if isinstance(data, dict):
            decoded = _decode_value(evt)
            if decoded is not None:
                data['decoded_event_data'] = decoded
            # Decode common error shapes for readability
            if data.get('event_name') == 'ExtrinsicFailed':
                de = _decode_extrinsic_failed(data.get('event_data', {}))
                if de:
                    data['decoded_error'] = de
            # ProxyExecuted may wrap Module errors under result -> Err -> Module
            if data.get('event_name') == 'ProxyExecuted':
                res = data.get('event_data', {}).get('result')
                if isinstance(res, dict) and res.get('name') == 'Err':
                    vals = res.get('values')
                    if isinstance(vals, list) and vals:
                        mod = vals[0]
                        if isinstance(mod, dict) and mod.get('name') == 'Module':
                            de = {'dispatch_error': {'name': 'Module', 'values': mod.get('values')}}
                            de = _decode_extrinsic_failed(de)
                            if de:
                                data['decoded_error'] = de

        print(f"[ა] Message received: {json.dumps(data, ensure_ascii=False)}")
    except Exception:
        print(f"[ა] Message received: {body.decode(errors='ignore')}")

# Start consumer (without attempting to declare the queue)
channel.basic_consume(
    queue=queue_name,
    on_message_callback=callback,
    auto_ack=True
)

try:
    channel.start_consuming()
except KeyboardInterrupt:
    print("\n[!] Consumer stopping...")
    channel.stop_consuming()

connection.close()
