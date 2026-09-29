import json
import os

PORTAL_DIR = os.path.dirname(os.path.abspath(__file__))
from http.server import HTTPServer, BaseHTTPRequestHandler

class MockPortalHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            with open(os.path.join(PORTAL_DIR, "index.html"), "r") as f:
                self.wfile.write(f.read().encode())
        
        elif self.path.startswith("/api/recruitments"):
            # Basic pagination parsing
            page = 1
            if "page=" in self.path:
                try:
                    page = int(self.path.split("page=")[1])
                except:
                    page = 1
            
            file_path = os.path.join(PORTAL_DIR, "data", f"page{page}.json")
            if not os.path.exists(file_path):
                self.send_response(404)
                self.end_headers()
                return
                
            with open(file_path, "r") as f:
                data = f.read()
                
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(data.encode())
        
        else:
            self.send_response(404)
            self.end_headers()

def run_server():
    server_address = ("", 8000)
    httpd = HTTPServer(server_address, MockPortalHandler)
    print("Mock Portal running on port 8000...")
    httpd.serve_forever()

if __name__ == "__main__":
    run_server()
