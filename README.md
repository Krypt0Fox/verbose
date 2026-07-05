# verbose

Runtime configuration loader and module manager.

## Setup
```
pip install -r deps.lock
python main.py
```

## Structure
```
├── main.py              # Entry point
├── runtime/
│   ├── __init__.py
│   └── cfg_loader.py    # Core config loader
├── config/
│   ├── __init__.py
│   └── settings.yaml    # Runtime settings
├── deps.lock            # Pinned dependencies
└── Procfile             # Process manager
```

## License
Proprietary
