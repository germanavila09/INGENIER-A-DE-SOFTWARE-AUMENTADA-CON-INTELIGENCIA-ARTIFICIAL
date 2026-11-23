# Reunión con stakeholders: pagos en línea (12/09)

## Necesidades mencionadas

- Los clientes necesitan pagar con tarjeta de crédito y débito sin salir del sitio.
- Cuando la tarjeta es rechazada, el cliente debe ver el motivo en lenguaje claro y poder reintentar.
- Finanzas quiere detectar transacciones sospechosas antes de cobrar; hoy lo hacen manualmente al día siguiente.
- Soporte pide un registro de intentos fallidos por cliente para atender reclamos.

## Restricciones

- Nunca guardar el número completo de la tarjeta ni el CVV.
- El cobro debe confirmarse por correo al cliente.
- La pasarela actual tiene un límite de 3 reintentos por hora por cuenta.
