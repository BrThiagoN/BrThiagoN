# Direção visual do perfil

## Contexto

Perfil de um estudante de Engenharia de Software da FIAP, estagiário de Produto e Desenvolvimento, com projetos de APIs, automação e IA aplicada. O leitor encontra este conteúdo dentro do retângulo do README, ao lado da coluna nativa do GitHub. A direção solicitada é japonesa, cyberpunk/cyberpink e corporativa, inspirada em Arasaka.

## Tokens

- Fundo: transparente, herdando visualmente o README do GitHub.
- Texto principal: `#f3ece8` no escuro / `#24292f` no claro.
- Texto secundário: `#a5a1ad` no escuro / `#59636e` no claro.
- Vermelho: `#f05c73` no escuro / `#b91f45` no claro.
- Rosa: `#ef8fb4` no escuro / `#96335f` no claro.
- Divisórias: `#3d3342` no escuro / `#d1d9e0` no claro.

O vermelho marca a identidade. O rosa aparece no foco profissional e no calendário. A intensidade do gráfico continua vindo do GitHub; apenas sua escala de cores muda.

O SVG usa a paleta escura como base e troca as cores com `prefers-color-scheme: light`. Em uma imagem SVG, essa consulta acompanha o `color-scheme` do elemento que a incorpora; o GitHub define essa propriedade conforme seu tema. Assim, o tema escolhido no site tem prioridade sobre um tema diferente no sistema. A ausência de suporte à consulta mantém a base escura. Não precisamos duplicar arquivos por tema.

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

## Movimento e nova revisão

A superfície preta da versão anterior ainda criava um segundo retângulo dentro do README. Removê-la faz o documento pertencer ao GitHub nos dois temas; a versão clara ganha cores mais profundas para manter contraste. O selo continua sendo a principal marca visual.

O calendário recebe uma única onda de entrada da esquerda para a direita: cada coluna começa depois da anterior, com leve defasagem entre dias. O movimento dura aproximadamente dois segundos e termina com todos os dias visíveis. No celular, as duas faixas fazem a mesma entrada simultaneamente. Textos, métricas e selo permanecem estáticos. A animação só é ativada quando `prefers-reduced-motion: no-preference` corresponde; movimento reduzido ou CSS sem suporte mostra o calendário completo imediatamente.

## Continuidade do rodapé

Na renderização real do GitHub, os títulos em código, links azuis e chips de stack do Markdown interrompiam a identidade do dashboard. O rodapé passa a usar a mesma paleta adaptativa e tipografia monoespaçada, em linhas SVG compactas e transparentes. Cada projeto mantém nome, stack e descrição; a linha inteira é um link HTML para o repositório. Os contatos usam links de texto desenhados em SVG, sem fundo de badge ou logos.

O painel principal permanece igual. Os assets do rodapé são separados porque imagens SVG no GitHub não permitem clicar em links internos. As linhas têm versões desktop/mobile e alturas calculadas pelo conteúdo. Os textos dos projetos ficam em `scripts/profile.json`; o README mantém apenas a composição e os destinos clicáveis. Todos os elementos do rodapé são estáticos, para concentrar o movimento no calendário.
