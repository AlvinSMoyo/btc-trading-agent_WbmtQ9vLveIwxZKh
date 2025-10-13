# app/__init__.py — load .env for all submodules
try:
    from dotenv import load_dotenv
    load_dotenv()  # loads .env from the project root (current working dir)
except Exception:
    pass
