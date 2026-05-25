---
name: polymarket_btc_updown
description: Investigacion profunda para mercados BTC Up/Down de Polymarket.
---

Usa este flujo cuando el usuario pida investigar BTC Up/Down:
1. Alinea ventana en ET/America/New_York.
2. Separa precio a superar, precio actual, prediccion de cierre y countdown.
3. Confirma order book, spread, liquidez, edge y Kelly.
4. Si falta book, precio de referencia o quedan pocos segundos al cierre, la decision es NO TRADE.
5. Guarda evidencia y artefactos para auditoria.
