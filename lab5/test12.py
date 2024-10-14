import sys
import socket
import json

def main(argv):
    obj1 = {
        'name': 'MJ Kim',
        'id': 12345678,
        'work': {
            'name': 'Myongji University',
            'address': '116 Myongji-ro'
        },
    }

    s = json.dumps(obj1)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
    sock.sendto(bytes(s, encoding='utf-8'), ('127.0.0.1', 10001))
    (data, sender) = sock.recvfrom(65536)

    obj2 = json.loads(str(data, 'utf-8'))
    print(obj2)

    sock.close()

if __name__ == '__main__':
    main(sys.argv)