import socket, sys
s = socket.create_connection(("127.0.0.1", 47811), 3); s.sendall((sys.argv[1] + "\n").encode()); print(s.makefile().readline())
