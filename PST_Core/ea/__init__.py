"""
EAs de MT5 gestionados desde la app y por agentes (rama feature/eas-mt5, ver MT5/DESARROLLO_EAS.md).

  rutas     donde estan los terminales, Common\\Files y la carpeta de control
  store     tablas ea_* de la BBDD (despliegues, eventos, backtests, configuracion)
  control   ficheros que leen los EAs y el gestor (control.csv, despliegue.csv, plantillas) y latidos
  mt5cli    compilar con MetaEditor y backtest con el terminal portable, por linea de comandos
  servicio  acciones de alto nivel. La app (PST_API/eas_api.py) y el MCP (PST_MCP) llaman SOLO aqui,
            para que el boton y la tool del agente hagan exactamente lo mismo.
"""
