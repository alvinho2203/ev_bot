import logging
from math import prod
from itertools import combinations

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================== LOGGING ================== #

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ================== MODELOS E FUNÇÕES BASE ================== #

class Bet:
    def __init__(self, name: str, odd_bet365: float, odd_pinnacle: float):
        self.name = name
        self.odd_bet365 = odd_bet365
        self.odd_pinnacle = odd_pinnacle

    @property
    def p_fair(self) -> float:
        # prob. "justa" ~ 1 / Pinnacle
        return 1.0 / self.odd_pinnacle

    @property
    def odd_fair(self) -> float:
        return 1.0 / self.p_fair

    @property
    def ev_single_simple(self) -> float:
        # EV aproximado da simples, só info
        return (self.odd_bet365 / self.odd_fair - 1.0) * 100.0


def build_multiples(bets, min_legs: int, max_legs: int):
    """
    Gera todas as múltiplas entre min_legs e max_legs, com:
      - odd Bet365 da multi
      - prob. real (Pinnacle)
      - odd justa da multi
    """
    multiples = []
    for r in range(min_legs, max_legs + 1):
        for combo in combinations(bets, r):
            names = [b.name for b in combo]
            odd365_multi = prod(b.odd_bet365 for b in combo)
            p_fair_multi = prod(b.p_fair for b in combo)  # prob. real
            odd_fair_multi = 1.0 / p_fair_multi

            multiples.append({
                "bets": names,
                "legs": r,
                "odd365": odd365_multi,
                "odd_fair": odd_fair_multi,
                "p_hit": p_fair_multi,
            })
    return multiples

# ========= STAKE SUGERIDA (FÓRMULA ANTIGA, EM % DA BANCA) ========= #

def calcular_stake_percent(fair_odd: float, odd_bet365: float) -> float:
    """
    Usa exatamente a fórmula antiga:

        stake = (((1 / fair_odd) * odd3 - 1) / (odd3 - 1)) * 0.2
        stake = round(stake / 0.0025) * 0.25

    Interpretação:
        - fair_odd   -> odd justa da múltipla (Pinnacle)
        - odd_bet365 -> odd Bet365 da múltipla
        - retorno: stake em % da banca (0.25 = 0.25%, 1.00 = 1%, etc.)

    Se não tiver valor, retorna 0.0.
    """
    if fair_odd <= 1.0 or odd_bet365 <= 1.0:
        return 0.0

    stake = (((1.0 / fair_odd) * odd_bet365 - 1.0) / (odd_bet365 - 1.0)) * 0.2
    stake = round(stake / 0.0025) * 0.25

    if stake <= 0:
        return 0.0

    return stake


# ================== HANDLERS ================== #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    context.user_data.setdefault("bets", [])

    msg = (
        "🔥 Bem-vindo ao *Gerador de Múltiplas EV+* (Alvinho TIPS)\n\n"
        "Envie picks no formato (uma por mensagem):\n"
        "`Descrição;odd_bet365;odd_pinnacle`\n\n"
        "Exemplos:\n"
        "`Curry over 27.5;1.90;1.71`\n"
        "`Warriors +3.5;2.00;1.83`\n\n"
        "Depois use:\n"
        "`/calcular min max ev_min stake_base top_n [bankroll]`\n\n"
        "Ex (sem banca):\n"
        "`/calcular 2 3 0 100 20`\n\n"
        "Ex (com banca de 2000):\n"
        "`/calcular 2 3 0 100 20 2000`"
    )

    await context.bot.send_message(chat_id, msg, parse_mode="Markdown")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["bets"] = []
    await update.message.reply_text("🔄 Apostas limpas. Pode enviar novas.")


async def receber_aposta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()

    if text.startswith("/"):
        return

    parts = [p.strip() for p in text.split(";")]
    if len(parts) < 3:
        await update.message.reply_text(
            "⚠️ Formato inválido.\nUse: Descrição;odd_bet365;odd_pinnacle\n"
            "Ex: Curry over 27.5;1.90;1.71"
        )
        return

    desc = parts[0]
    try:
        odd365 = float(parts[1].replace(",", "."))
        oddpin = float(parts[2].replace(",", "."))
    except ValueError:
        await update.message.reply_text("⚠️ Odds inválidas. Use números tipo 1.90;1.71.")
        return

    if odd365 <= 1.0 or oddpin <= 1.0:
        await update.message.reply_text("⚠️ Odds devem ser > 1.0.")
        return

    bets = context.user_data.setdefault("bets", [])
    bets.append(Bet(desc, odd365, oddpin))

    await update.message.reply_text(
        f"✅ Aposta adicionada:\n"
        f"{desc}\n"
        f"Bet365: {odd365} | Pinnacle: {oddpin}\n"
        f"Total cadastradas: {len(bets)}"
    )


async def calcular(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bets = context.user_data.get("bets", [])
    if len(bets) < 2:
        await update.message.reply_text("⚠️ Cadastre pelo menos 2 picks antes de calcular.")
        return

    try:
        args = context.args
        min_legs = int(args[0]) if len(args) >= 1 else 2
        max_legs = int(args[1]) if len(args) >= 2 else min(3, len(bets))
        ev_min = float(args[2]) if len(args) >= 3 else 0.0
        stake_base = float(args[3]) if len(args) >= 4 else 100.0
        top_n = int(args[4]) if len(args) >= 5 else 20
        bankroll = float(args[5]) if len(args) >= 6 else 0.0
    except Exception:
        await update.message.reply_text(
            "⚠️ Uso correto:\n"
            "`/calcular min max ev_min stake_base top_n [bankroll]`\n"
            "Ex: `/calcular 2 3 0 100 20 2000`",
            parse_mode="Markdown",
        )
        return

    if min_legs < 2:
        min_legs = 2
    if max_legs < min_legs:
        max_legs = min_legs
    if max_legs > len(bets):
        max_legs = len(bets)

    multiples = build_multiples(bets, min_legs, max_legs)
    resultados = []

    for m in multiples:
        O = m["odd365"]
        p = m["p_hit"]
        odd_fair = m["odd_fair"]

        # EV% clássico com prob real
        ev_percent = (O * p - 1.0) * 100.0
        if ev_percent < ev_min:
            continue

        lucro_est = stake_base * (ev_percent / 100.0)

        # stake sugerida em % (fórmula antiga)
        stake_pct = calcular_stake_percent(odd_fair, O)
        if bankroll > 0:
            stake_val = bankroll * (stake_pct / 100.0)
        else:
            stake_val = 0.0

        resultados.append({
            "legs": m["legs"],
            "bets": m["bets"],
            "odd_fair": odd_fair,
            "odd365": O,
            "ev": ev_percent,
            "lucro": lucro_est,
            "p": p,
            "stake_pct": stake_pct,
            "stake_val": stake_val,
        })

    if not resultados:
        await update.message.reply_text("Nenhuma múltipla atingiu o EV mínimo.")
        return

    resultados.sort(key=lambda x: x["ev"], reverse=True)
    top = resultados[:top_n]

    msg = "📊 *TOP MÚLTIPLAS EV+*\n\n"
    for i, r in enumerate(top, 1):
        picks_str = " / ".join(r["bets"])
        msg += (
            f"#{i} — {r['legs']} seleções\n"
            f"{picks_str}\n"
            f"Prob. real de bater (Pinnacle): {r['p']*100:.2f}%\n"
            f"Odd justa (multi): {r['odd_fair']:.3f}\n"
            f"Odd Bet365 (multi): {r['odd365']:.3f}\n"
            f"EV da múltipla: {r['ev']:.2f}%\n"
            f"Lucro esperado com stake R$ {stake_base:.2f}: R$ {r['lucro']:.2f}\n"
            f"Stake sugerida (fórmula antiga): {r['stake_pct']:.2f}% da banca\n"
        )

        if r["stake_val"] > 0:
            msg += f"Stake sugerida em R$ (banca {bankroll:.2f}): R$ {r['stake_val']:.2f}\n"

        msg += "------------------------\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def error_handler(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Erro no bot:", exc_info=context.error)


def main():
    TOKEN = os.getenv("TOKEN")

    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(CommandHandler("calcular", calcular))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_aposta))

    application.add_error_handler(error_handler)

    application.run_polling()


if __name__ == "__main__":
    main()
