# Activación directa por voz en español

## Objetivo

Permitir órdenes fluidas en una sola frase, por ejemplo «Jarvis, abre Spotify», sin depender del modelo inglés `hey_jarvis` y sin aceptar ruido como activación.

## Diseño

El bucle de voz usará el VAD existente para detectar el inicio y el final de una frase. Transcribirá esa ventana una sola vez con el proveedor STT ya configurado. Una transcripción solo activará Jarvis cuando, tras normalizar mayúsculas, acentos y puntuación, comience por la palabra independiente `jarvis`.

La activación extraerá el texto posterior a `jarvis` y lo entregará directamente al gestor de turnos. Si la frase contiene únicamente la palabra de activación, Jarvis conservará el estado activado y escuchará una segunda frase durante el tiempo de conversación existente. Si no comienza por `jarvis`, descartará la transcripción sin responder.

El modo `wake_word` pasará a usar esta activación por transcripción. Los demás modos conservarán su comportamiento. No se añadirá ninguna dependencia ni servicio: se reutilizarán el VAD, STT y `ActivationManager` actuales.

## Flujo

1. Capturar audio hasta que el VAD delimite una frase.
2. Transcribir la frase con Whisper en español.
3. Normalizar y comprobar el prefijo `jarvis` como palabra completa.
4. Si hay una orden restante, procesarla en el mismo turno.
5. Si solo se dijo `jarvis`, escuchar la orden siguiente.
6. Si no hay activación, volver a espera silenciosamente.

## Errores y límites

- Una transcripción vacía o un fallo recuperable de STT vuelve al estado de espera.
- Coincidencias internas como «hablé con Jarvis» no activan el sistema.
- La precisión depende de Whisper, pero evita rebajar umbrales acústicos y los falsos positivos observados.
- La primera respuesta tendrá el coste de una transcripción; no habrá una segunda transcripción cuando activación y orden estén en la misma frase.

## Verificación

- Pruebas unitarias para prefijo válido, puntuación, frase sin activación, coincidencia interna y activación sin orden.
- Prueba del bucle que confirma que «Jarvis, abre Spotify» produce un solo turno con `abre Spotify`.
- Prueba que confirma que ruido o texto sin `jarvis` no crea turnos.
- Prueba manual en Windows con el micrófono disponible y `config.win.json`.
