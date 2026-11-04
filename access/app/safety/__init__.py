"""Niveis de seguranca por pessoa (Spec 21 §2).

Nivel 1 = sem trava (padrao, o produto): o pedido segue o caminho normal e NENHUM modulo deste pacote e importado nem executado.
Nivel 2 = com classificadores: so entao `app.safety.flow` e carregado.

Os dois classificadores sao separados de proposito (nunca misturar):
- `classifier_input`  (classificador 1): olha o que a pessoa PEDE, antes de chegar ao modelo.
- `classifier_output` (classificador 2): olha o que o modelo RESPONDE e as ACOES do agente (chamadas de ferramenta), antes de entregar.
Cada um tem modulo, configuracao (chave propria em `settings`), log (`safety_log.classifier`), interruptor e rota de painel proprios.
"""
