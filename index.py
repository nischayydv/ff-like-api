import os
import sys

# Make sure sibling modules (app.py, like_pb2.py, etc.) are importable on Vercel
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app  # Vercel's Python runtime looks for a WSGI object named `app`

# Optional: simple health route so you can check the deployment is alive
@app.route('/')
def home():
    return {"status": "running", "usage": "/like?uid=<UID>&server_name=<IND|BR|US|SAC|NA|BD>"}


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5001)))
