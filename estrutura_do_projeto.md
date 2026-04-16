estrutura:

agrovision_ia/
│
├── .venv/                  # ambiente virtual Python
├── app.py                  # aplicação FastAPI
├── requirements.txt        # lista de dependências
├── README.md               # instruções do projeto
├── .gitignore
├── static/                 # arquivos estáticos (css, js, imagens da interface)
├── templates/              # páginas HTML do FastAPI/Jinja2
├── uploads/                # imagens enviadas para teste
├── runs/                   # saídas do YOLO (predições e treinos)
├── models/                 # pesos próprios, se houver
└── dataset_agro/
    ├── images/
    │   ├── train/
    │   └── val/
    ├── labels/
    │   ├── train/
    │   └── val/
    └── data.yaml