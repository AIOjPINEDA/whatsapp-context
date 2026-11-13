<p align="center"><img src="docs/assets/banner.png" alt="whatsapp-context" width="100%"></p>

**Deja que Claude, ChatGPT o cualquier asistente de IA local lea los chats de WhatsApp que elijas. Solo lectura, en tu Mac.**

![macOS 14+](https://img.shields.io/badge/macOS-14%2B-555555) ![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB) ![MCP stdio](https://img.shields.io/badge/MCP-stdio-6366f1) ![License MIT](https://img.shields.io/badge/license-MIT-blue)

[English](README.md) · [Web](https://aiojpineda.github.io/whatsapp-context/) · [Seguridad](SECURITY.md)

## Qué puedes preguntar

- *«Priorízame el WhatsApp de las últimas 24 horas. ¿Qué tengo que contestar?»*
- *«Ponme al día del grupo de la reforma de la cocina desde el lunes.»*
- *«Busca el presupuesto del fontanero que me mandaron y resúmeme la conversación.»*

El asistente te propone respuestas como texto y tú las envías: no hay herramienta de envío.

## Cómo funciona

```text
WhatsApp para Mac ──▶ ChatStorage.sqlite (protegido por macOS)
                          │ solo lectura
                  WAExport.app  ← el único programa con Acceso total al disco
                          │ export: índice de todos los chats + texto solo de los permitidos
     servidor MCP + CLI wa (Python, sin permisos especiales) ◀── config/policy.toml
                          │ stdio
          Claude Code · Claude Desktop · ChatGPT / Codex · cualquier cliente MCP local
```

El export (`~/.local/share/whatsapp-context`) se renueva solo pasados 15 minutos o al cambiar la política.

## Instalación

Requisitos:
- macOS 14 o posterior
- WhatsApp para Mac, con la sesión iniciada y sincronizado
- Xcode Command Line Tools (`xcode-select --install`)
- [uv](https://docs.astral.sh/uv/) (`brew install uv`); instala Python 3.12 si hace falta

```bash
git clone https://github.com/AIOjPINEDA/whatsapp-context.git ~/whatsapp-context
```

```bash
cd ~/whatsapp-context
```

```bash
make setup
```

`make setup` comprueba los requisitos, instala `/Applications/WAExport.app`, crea `config/policy.toml`,
abre Ajustes del Sistema para que **tú** actives el Acceso total al disco de WAExport (**solo** WAExport:
nunca Terminal ni una app de IA) y ofrece conectar cada asistente que encuentre. Puedes repetirlo sin miedo.
Si tu usuario no puede escribir en `/Applications`, te pide arrastrar WAExport allí en Finder.

## Instalación con tu agente de IA

Pega esto en Claude Code, Codex o cualquier agente que pueda ejecutar comandos en tu Mac:

```text
Clona https://github.com/AIOjPINEDA/whatsapp-context en ~/whatsapp-context y sigue su INSTALL.md paso a paso. Pregúntame antes de cada paso que necesite mi acción.
```

[INSTALL.md](INSTALL.md) es el guion del agente. Se para en todo lo que solo puedes hacer tú y nunca elige chats por ti.

## Elige qué puede leer

De serie, el asistente **no** lee texto de ningún mensaje: solo nombres de chat, horas y no leídos. Busca
los nombres exactos con `uv run wa chats --all --grep cocina` (cada chat muestra su `[JID]`) y
edita `config/policy.toml`:

```toml
[content]
chats = ["Club de lectura", "+34 600 000 000"]   # nombres exactos, teléfonos o JID
people = []                                      # grupos donde esta persona participa

[projects."Reforma cocina"]
context = "~/Documents/Cocina"                   # carpeta (su README.md) o fichero que lee el asistente
chats = ["Obra cocina"]
patterns = ['^Cocina ']                          # regex sobre el nombre del chat

[exclude]
chats = ["Familia"]                              # nunca aparece, ni como metadato

[options]
days = 180
```

- Precedencia: `exclude` > `chats` > `patterns` > `people`. Da igual mayúsculas y espacios de más.
- Un nombre que comparten varios chats no da acceso a nada (salvo que sean el teléfono y el ID vinculado
  de una misma persona): usa el teléfono o el JID.
- Compruébalo con `uv run wa status`: muestra las líneas `Policy entry with no match` y `Policy conflict`.
- Lo que pidas desde el chat (*«dale acceso al grupo X en el proyecto Y»*, *«revoca X»*, *«excluye X»*)
  se escribe solo en `config/policy-claude.toml`, por JID con el nombre como comentario, y nunca puede
  levantar tus exclusiones. Al revocar te avisa si otra regla sigue dando acceso al chat.
- La búsqueda devuelve 50 resultados, los más recientes, y avisa cuando se corta; súbelo con
  `uv run wa search "presupuesto" --limit 200`.

## Asistentes compatibles

| Asistente | Se registra con |
|---|---|
| Claude Code | `make register-code` |
| Claude Desktop | `make register-desktop` (cierra la app con Cmd+Q cuando te lo pida) |
| ChatGPT para Mac, Codex CLI y extensión de IDE | `make register-codex` y reinicia la app |
| Cualquier otro cliente MCP local | `make show-mcp` y pega el JSON en ese cliente |

claude.ai y chatgpt.com en el navegador necesitan servidores remotos; este es local a propósito.

## Privacidad y seguridad

- **Solo lectura.** WAExport abre la base de datos de WhatsApp en solo lectura, no usa la red y no puede enviar nada.
- **Solo los chats permitidos llevan texto.** El resto son metadatos; el índice nunca guarda el texto del
  último mensaje, borradores ni claves de multimedia. Los de `[exclude]` no aparecen en ninguna
  herramienta ni vista de `wa` (su nombre sigue en el índice local de la exportación).
- **El proveedor de tu asistente recibe todo lo que el asistente lee**, como cualquier otra cosa que le enseñes.
- **La política filtra las herramientas MCP, no tu cuenta.** Un asistente con acceso a la shell actúa como
  tú y podría leer el export directamente. El texto de los mensajes no es fiable: mantén las aprobaciones.

Modelo de amenazas y cómo avisar de un fallo: [SECURITY.md](SECURITY.md).

## Actualizar, desinstalar, problemas

Actualizar: `git pull` en `~/whatsapp-context`, luego `make setup` y reinicia tus asistentes. Si el
[CHANGELOG](CHANGELOG.md) dice que WAExport cambió, vuelve a darle el Acceso total al disco (ver abajo).

Desinstalar: cierra Claude Desktop y ejecuta `make uninstall`. Borra WAExport, el export y las entradas
de este repo en los clientes; si no puede quitar una entrada, lo dice, sale con error y conserva WAExport y el export.
Después quita WAExport del Acceso total al disco y borra la carpeta.

| Si ves | Haz esto |
|---|---|
| `WAExport has no Full Disk Access` | Ajustes del Sistema > Privacidad y seguridad > Acceso total al disco: añade `/Applications/WAExport.app` y ejecuta `uv run wa refresh` |
| Dejó de funcionar tras actualizar | WAExport va firmado ad hoc y una compilación nueva pierde el permiso: quítalo (-) y vuelve a añadirlo (+) |
| `(is WhatsApp open on this Mac?)` | Abre WhatsApp para Mac y deja que sincronice |
| `Policy entry with no match` | Copia el nombre exacto de `uv run wa chats --all`; si pone *ambiguous*, usa el teléfono o el `[JID]` |
| `WARNING: the process running this tool ...` (siempre la primera línea) | Quita el Acceso total al disco a esa app; solo WAExport debe tenerlo |
| Zona horaria incorrecta | Define `WA_TZ`, p. ej. `Europe/Madrid` |

## Preguntas frecuentes

- **¿Puede enviar mensajes?** No, y nunca podrá.
- **¿Vincula un dispositivo o usa la API de WhatsApp?** No. Lee lo que WhatsApp para Mac ya ha guardado.
- **¿Windows, Linux, iPhone o Android?** No. Solo macOS con WhatsApp para Mac.
- **¿Lo romperá una actualización de WhatsApp?** Puede; `uv run wa status` dice qué ha cambiado.
- **¿Puede el asistente darse acceso a más chats?** Solo con `whatsapp_allow`, que tu cliente debería pedirte aprobar cada vez.
- **¿Encuentra un chat que ya no sale en la bandeja?** Sí: `whatsapp_chats` con `all` (y `grep`) lista todos los chats.

## Licencia

[MIT](LICENSE). Contribuciones: [CONTRIBUTING.md](CONTRIBUTING.md).

Proyecto independiente, sin relación con WhatsApp ni Meta ni respaldado por ellas. Las marcas pertenecen a
sus propietarios. Úsalo con tu propia cuenta y respeta la privacidad de quienes te escriben.
