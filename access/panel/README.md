# Painel do llm-access

Frontend do administrador (React 18 + Vite + Tailwind v4 + lucide-react). Contrato: `access/DESIGN.md`, secao "API do painel". Visual no padrao do 9Router (paleta coral, cartoes, emblemas, claro/escuro).

## Rodar

```bash
npm install
VITE_MOCK=1 npm run dev     # http://localhost:5175/admin/  (senha do mock: admin)
npm run build               # saida em dist/ (base /admin/), sem o mock
```

No PowerShell: `$env:VITE_MOCK=1; npm run dev`.

O mock (`src/lib/mock.js`) implementa todo o contrato em memoria. Parametros uteis na URL: `?mock_state=starting` (GPU ligando), `?mock_error=1` (aviso no `error` de /machines). Buscar "erro" em Pessoas simula falha de listagem.

## Estrutura

- `src/lib/api.js`: cliente (`/admin/api/...`, `credentials: 'same-origin'`, 401 volta ao login)
- `src/components/ui.jsx`: Button, Card, Badge, Input, Toggle, Drawer, Modal, Toast, estados
- `src/pages/`: Pessoas, Uso, Maquinas, Configuracoes
- `screenshots/`: telas em claro/escuro, desktop/celular (geradas com Playwright sobre o mock)
