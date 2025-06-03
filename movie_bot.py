# movie_bot.py
import logging
import os
import requests
import random
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, JobQueue
from datetime import datetime, timedelta

# Cargar variables de entorno
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

# Configuración de logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constantes de TMDB
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

# Almacenamiento de géneros (se llenará al inicio)
GENRE_MAP = {}

# --- Funciones Auxiliares para TMDB ---
async def fetch_tmdb_data(endpoint: str, params: dict = None) -> dict:
    if params is None:
        params = {}
    params['api_key'] = TMDB_API_KEY
    params['language'] = 'es-ES'
    params['include_adult'] = 'false' # No incluir contenido para adultos

    try:
        response = requests.get(f"{TMDB_BASE_URL}/{endpoint}", params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data from TMDB: {e}")
        return {}

def escape_markdown_v2(text: str) -> str:
    """Escapa caracteres especiales para MarkdownV2."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return "".join(f'\\{char}' if char in escape_chars else char for char in str(text))

async def send_movie_with_poster(update_or_query, context: ContextTypes.DEFAULT_TYPE, movie: dict, intro_text: str = ""):
    """Envía los detalles de una película con su póster."""
    title = escape_markdown_v2(movie.get('title', 'N/A'))
    overview = escape_markdown_v2(movie.get('overview', 'Sin descripción.')[:250] + "...") # Acortar
    release_date = escape_markdown_v2(movie.get('release_date', 'N/A'))
    vote_average = escape_markdown_v2(movie.get('vote_average', 0))
    movie_id = movie.get('id')
    tmdb_url = f"https://www.themoviedb.org/movie/{movie_id}" if movie_id else None

    caption = f"{escape_markdown_v2(intro_text)}\n" if intro_text else ""
    caption += f"🎬 *{title}*\n"
    caption += f"🗓️ Estreno: {release_date}\n"
    caption += f"⭐ Puntuación: {vote_average}/10\n"
    caption += f"📝 Sinopsis: {overview}\n"
    if tmdb_url:
        caption += f"[Ver en TMDB]({escape_markdown_v2(tmdb_url)})\n"

    poster_path = movie.get('poster_path')
    chat_id = update_or_query.effective_chat.id

    if poster_path:
        poster_url = f"{TMDB_IMAGE_BASE_URL}{poster_path}"
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=poster_url,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error sending photo: {e}. Sending text fallback.")
            await context.bot.send_message(chat_id, caption, parse_mode=ParseMode.MARKDOWN_V2, disable_web_page_preview=False)
    else:
        await context.bot.send_message(chat_id, caption, parse_mode=ParseMode.MARKDOWN_V2, disable_web_page_preview=False)

# --- Carga de Géneros ---
async def load_genres(context: ContextTypes.DEFAULT_TYPE) -> None:
    global GENRE_MAP
    data = await fetch_tmdb_data("genre/movie/list")
    if data and data.get('genres'):
        GENRE_MAP = {genre['id']: genre['name'] for genre in data['genres']}
        logger.info(f"Géneros cargados: {len(GENRE_MAP)}")
    else:
        logger.error("No se pudieron cargar los géneros.")

# --- Comandos del Bot y Handlers de Mensajes ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    keyboard = [
        [KeyboardButton("🌟 Populares"), KeyboardButton("🏆 Mejor Valoradas")],
        [KeyboardButton("📅 Estrenos"), KeyboardButton("✨ Recomendar Película")],
        [KeyboardButton("🔍 Buscar Película (texto)")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False) # False para que persista un poco
    await update.message.reply_html(
        f"¡Hola {user.mention_html()}! 👋 Soy tu bot de películas.\n"
        "Elige una opción del teclado o usa /ayuda para ver más comandos.",
        reply_markup=reply_markup
    )

async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "🤖 *Comandos y Funciones del Bot:*\n\n"
        "*Menú Principal (Teclado):*\n"
        "  🌟 *Populares:* Muestra las películas más populares del momento\.\n"
        "  🏆 *Mejor Valoradas:* Lista de películas con mejores puntuaciones\.\n"
        "  📅 *Estrenos:* Elige ver estrenos del mes o del año\.\n"
        "  ✨ *Recomendar Película:* Te ayudo a encontrar una película por género\.\n"
        "  🔍 *Buscar Película (texto):* Envía el nombre de una película para buscarla\.\n\n"
        "*Otros Comandos:*\n"
        "  `/start` \- Iniciar el bot y mostrar menú principal\.\n"
        "  `/ayuda` \- Mostrar esta ayuda\.\n"
        "  `/buscar <título>` \- Buscar una película directamente por título\.\n"
        "  `/cerrar_teclado` \- Ocultar el teclado de opciones principal\."
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=ReplyKeyboardRemove())

async def cerrar_teclado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Teclado principal oculto\. Puedes volver a mostrarlo con /start\.", reply_markup=ReplyKeyboardRemove())

async def populares_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    processing_msg = await update.message.reply_text("Buscando películas populares... ⏳")
    data = await fetch_tmdb_data("movie/popular")
    await processing_msg.delete()
    if data and data.get('results'):
        await update.message.reply_text(escape_markdown_v2("🌟 Estas son las películas más populares del momento:"), parse_mode=ParseMode.MARKDOWN_V2)
        for movie in data['results'][:5]:
            await send_movie_with_poster(update, context, movie)
    else:
        await update.message.reply_text("No pude encontrar películas populares en este momento. 😕")

async def mejor_valoradas_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    processing_msg = await update.message.reply_text("Buscando películas mejor valoradas... ⏳")
    data = await fetch_tmdb_data("movie/top_rated", params={'vote_count.gte': 1000}) # Que tengan bastantes votos
    await processing_msg.delete()
    if data and data.get('results'):
        await update.message.reply_text(escape_markdown_v2("🏆 Estas son algunas de las películas mejor valoradas:"), parse_mode=ParseMode.MARKDOWN_V2)
        for movie in data['results'][:5]:
            await send_movie_with_poster(update, context, movie)
    else:
        await update.message.reply_text("No pude encontrar películas mejor valoradas en este momento. 😕")

async def buscar_pelicula_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Por favor, incluye un título después del comando\. Ejemplo: `/buscar Matrix`", parse_mode=ParseMode.MARKDOWN_V2)
        return
    query = " ".join(context.args)
    await _do_search_movie(update, context, query)

async def buscar_pelicula_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Este handler se activa después de que el usuario presiona "🔍 Buscar Película (texto)"
    # y luego envía un mensaje de texto.
    # Podríamos usar ConversationHandler para esto, pero para simplificar, asumimos
    # que el siguiente mensaje de texto es la búsqueda.
    # Mejor: pedir explícitamente que escriba el nombre.
    if context.user_data.get('esperando_busqueda'):
        query = update.message.text
        await _do_search_movie(update, context, query)
        context.user_data['esperando_busqueda'] = False # Resetear
    else:
        # Si no está esperando búsqueda, es un mensaje normal, lo ignoramos o respondemos genéricamente
        # await update.message.reply_text("Si quieres buscar una película, usa el botón o el comando /buscar <título>.")
        pass # Ignorar mensajes de texto aleatorios si no se espera una búsqueda

async def solicitar_busqueda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Activado por el botón 'Buscar Película (texto)'."""
    context.user_data['esperando_busqueda'] = True
    await update.message.reply_text("Ok, ahora escribe el nombre de la película que quieres buscar y envíalo\. ✍️", reply_markup=ReplyKeyboardRemove())


async def _do_search_movie(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
    processing_msg = await update.message.reply_text(f"Buscando '{escape_markdown_v2(query)}'... ⏳")
    data = await fetch_tmdb_data("search/movie", params={'query': query})
    await processing_msg.delete()

    if data and data.get('results'):
        await update.message.reply_text(f"🔍 *Resultados para '{escape_markdown_v2(query)}':*", parse_mode=ParseMode.MARKDOWN_V2)
        for movie in data['results'][:3]:
            await send_movie_with_poster(update, context, movie)
    else:
        await update.message.reply_text(f"No encontré películas con el título '{escape_markdown_v2(query)}'. 😕")


# --- Funciones para Estrenos ---
async def estrenos_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("Estrenos de este Mes", callback_data='estrenos_mes')],
        [InlineKeyboardButton("Próximos Estrenos (Año)", callback_data='estrenos_ano')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Elige qué estrenos quieres ver:', reply_markup=reply_markup)

async def handle_estrenos_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer() # Confirmar recepción del callback
    
    choice = query.data
    
    text_to_send = f"Buscando {choice.replace('estrenos_', '').replace('_', ' ')}... ⏳"
    await query.edit_message_text(text=escape_markdown_v2(text_to_send), parse_mode=ParseMode.MARKDOWN_V2)
    
    params = {}
    title_text = ""
    today = datetime.today()

    if choice == 'estrenos_mes':
        first_day_of_month = today.replace(day=1).strftime('%Y-%m-%d')
        next_month_first_day = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        last_day_of_month = (next_month_first_day - timedelta(days=1)).strftime('%Y-%m-%d')
        
        params = {
            'primary_release_date.gte': first_day_of_month,
            'primary_release_date.lte': last_day_of_month,
            'sort_by': 'popularity.desc',
            'region': 'ES' # Ajusta a tu región o quítalo para global
        }
        title_text = "📅 *Estrenos de este Mes:*\n"
    elif choice == 'estrenos_ano':
        params = {
            'primary_release_year': today.year,
            'primary_release_date.gte': today.strftime('%Y-%m-%d'),
            'sort_by': 'primary_release_date.asc',
            'region': 'ES'
        }
        title_text = "🗓️ *Próximos Estrenos del Año:*\n"
    else:
        await query.edit_message_text(text="Opción no válida.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    data = await fetch_tmdb_data("discover/movie", params=params)
    
    # Borrar el mensaje "Buscando..." y enviar los resultados
    await query.message.delete() # Borra el mensaje original del botón
    
    if data and data.get('results'):
        await context.bot.send_message(query.effective_chat.id, escape_markdown_v2(title_text), parse_mode=ParseMode.MARKDOWN_V2)
        for movie in data['results'][:5]:
            await send_movie_with_poster(query, context, movie) # query tiene .effective_chat.id
    else:
        await context.bot.send_message(query.effective_chat.id, "No pude encontrar estrenos para esta selección. 😕", parse_mode=ParseMode.MARKDOWN_V2)

# --- Funciones para Recomendaciones por Género ---
async def recomendar_menu_genero_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not GENRE_MAP:
        await update.message.reply_text("Lo siento, los géneros no están disponibles para cargar en este momento. Intenta más tarde. 😕")
        return

    keyboard = []
    row = []
    # Mostrar una selección de géneros, o implementar paginación si son muchos
    # Ejemplo: Tomar los primeros 10 o algunos populares
    genres_to_show = list(GENRE_MAP.items())[:12] # Muestra los primeros 12 géneros

    for genre_id, genre_name in genres_to_show:
        row.append(InlineKeyboardButton(genre_name, callback_data=f'recom_genero_{genre_id}'))
        if len(row) >= 2: # 2 o 3 botones por fila
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    if not keyboard:
        await update.message.reply_text("No hay géneros disponibles para seleccionar.")
        return

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Elige un género para tu recomendación:', reply_markup=reply_markup)

async def handle_recomendar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        genre_id = int(query.data.split('_')[-1])
    except (IndexError, ValueError):
        await query.edit_message_text("Error al procesar la selección de género. 😕")
        return

    genre_name = GENRE_MAP.get(genre_id, "Desconocido")
    await query.edit_message_text(text=f"Buscando una recomendación de *{escape_markdown_v2(genre_name)}*... ⏳", parse_mode=ParseMode.MARKDOWN_V2)

    params = {
        'with_genres': str(genre_id),
        'sort_by': 'popularity.desc',
        'vote_count.gte': 100 # Películas con un mínimo de votos
    }
    data = await fetch_tmdb_data("discover/movie", params=params)
    
    await query.message.delete() # Borra el mensaje original del botón

    if data and data.get('results'):
        movie_to_recommend = random.choice(data['results'])
        await send_movie_with_poster(query, context, movie_to_recommend, intro_text=f"✨ Te recomiendo esta película de {genre_name}:")
    else:
        await context.bot.send_message(query.effective_chat.id, f"No pude encontrar recomendaciones para el género *{escape_markdown_v2(genre_name)}* en este momento. 😕", parse_mode=ParseMode.MARKDOWN_V2)

# --- Main ---
def main() -> None:
    if not TELEGRAM_BOT_TOKEN or not TMDB_API_KEY:
        logger.error("¡Error Crítico! TELEGRAM_BOT_TOKEN o TMDB_API_KEY no están configurados.")
        return

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Cargar géneros al inicio (una sola vez)
    # Usamos job_queue.run_once para que se ejecute después de que el event loop inicie
    if application.job_queue:
        application.job_queue.run_once(load_genres, 0) 
    else: # Para pruebas o entornos sin job_queue explícito, podrías llamarlo directamente, pero es mejor con run_once
        logger.warning("JobQueue no disponible, géneros no se cargarán automáticamente al inicio.")


    # Comandos
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ayuda", ayuda))
    application.add_handler(CommandHandler("buscar", buscar_pelicula_command))
    application.add_handler(CommandHandler("cerrar_teclado", cerrar_teclado))

    # Handlers para los botones del ReplyKeyboard (usando regex para el texto del botón)
    application.add_handler(MessageHandler(filters.Regex('^🌟 Populares$'), populares_handler))
    application.add_handler(MessageHandler(filters.Regex('^🏆 Mejor Valoradas$'), mejor_valoradas_handler))
    application.add_handler(MessageHandler(filters.Regex('^📅 Estrenos$'), estrenos_menu_handler))
    application.add_handler(MessageHandler(filters.Regex('^✨ Recomendar Película$'), recomendar_menu_genero_handler))
    application.add_handler(MessageHandler(filters.Regex('^🔍 Buscar Película \(texto\)$'), solicitar_busqueda))

    # Handler para mensajes de texto (para cuando se espera una búsqueda)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, buscar_pelicula_message_handler))
    
    # Callbacks para InlineKeyboards
    application.add_handler(CallbackQueryHandler(handle_estrenos_callback, pattern='^estrenos_'))
    application.add_handler(CallbackQueryHandler(handle_recomendar_callback, pattern='^recom_genero_'))

    logger.info("Bot iniciado. Presiona Ctrl+C para detener.")
    application.run_polling()

if __name__ == "__main__":
    main()
