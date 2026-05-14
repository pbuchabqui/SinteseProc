import ast
from pathlib import Path


def _chama_st_stop(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "stop"
            and isinstance(func.value, ast.Name)
            and func.value.id == "st"
        ):
            return True
    return False


def test_app_nao_bloqueia_inicialmente_sem_groq_api_key():
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))

    bloqueios_por_chave = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "not GROQ_KEY"
        and any(_chama_st_stop(item) for item in node.body)
    ]

    assert bloqueios_por_chave == []
