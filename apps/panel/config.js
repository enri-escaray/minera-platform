/* Configuración por sitio del panel. Se carga antes de app.js.
 * En dev apunta a los servicios locales; en producción cada sitio monta su
 * propio config.js sobre el nginx (mismo mecanismo, otras URLs). */
window.PANEL_URLS = {
  trazabilidad: "http://localhost:8000",
  mantenimiento: "http://localhost:8002",
  despacho: "http://localhost:8003",
  reportes: "http://localhost:8004",
};
window.PANEL_REFRESCO_MS = 10000;
