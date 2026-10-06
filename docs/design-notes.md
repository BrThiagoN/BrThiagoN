# Direção visual do perfil

## Contexto

Perfil de um estudante de Engenharia de Software da FIAP, estagiário de Produto e Desenvolvimento, com projetos de APIs, automação e IA aplicada. O leitor encontra este conteúdo dentro do retângulo do README, ao lado da coluna nativa do GitHub. A direção solicitada é japonesa, cyberpunk/cyberpink e corporativa, inspirada em Arasaka.

## Tokens

- Fundo: `#08090d`.
- Grafite: `#171820`.
- Texto principal: `#f3ece8`.
- Texto secundário: `#a5a1ad`.
- Vermelho: `#ed4259`.
- Rosa: `#ef8fb4`.

O vermelho marca a identidade. O rosa aparece no foco profissional e no calendário. A intensidade do gráfico continua vindo do GitHub; apenas sua escala de cores muda.

O nome usa uma fonte industrial condensada, com fallbacks locais Bahnschrift, DIN Condensed, Nimbus Sans Narrow e Arial Narrow. Os dados e os comandos usam fontes monoespaçadas do sistema. O selo japonês “開発” significa desenvolvimento; seus glifos são vetoriais, sem depender de uma fonte japonesa no navegador.

## Composição

Alinhamento à esquerda, viewBox desktop de 880 px, pensado para aproximadamente 846 px úteis no README. A identidade ocupa uma faixa superior; atividade, métricas e stack formam blocos de leitura do documento, sem simular outra tela do GitHub.

```text
┌─ README do GitHub ──────────────────────────────┐
│ Thiago Nascimento                         開    │
│ FIAP / estágio / backend                   発    │
│                                                │
│ Contribuições                  período real    │
│ [ calendário dos últimos 12 meses ]             │
│                                                │
│ Repos       Followers     Following     Stars   │
│                                                │
│ languages   Java / Python / ...                 │
│ backend     Node.js / Spring / ...              │
│ data        PostgreSQL                         │
│ tools       Linux / Git / ...                  │
├────────────────────────────────────────────────┤
│ Projetos e contatos em Markdown nativo          │
└────────────────────────────────────────────────┘
```

## Revisão da proposta

A primeira composição repetia uma sidebar e uma barra de aplicativo dentro do perfil, com cards arredondados e índices sem sequência real. A revisão remove esses elementos: o próprio GitHub já oferece essa estrutura. A personalidade fica concentrada na tipografia e no selo do cabeçalho. O restante prioriza texto legível, dados verificáveis e espaço consistente.

As prévias devem mostrar o retângulo completo do README, incluindo projetos e contatos, na largura realista de desktop e celular. São simulações para revisão visual, e não capturas de uma versão publicada.
