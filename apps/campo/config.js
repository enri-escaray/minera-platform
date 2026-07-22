/* Configuración por sitio de la PWA de campo. Se carga antes de app.js.
 * En dev apunta al servicio de reportes del compose; en producción cada sitio
 * monta su propio config.js sobre el nginx (mismo mecanismo, otra URL). */
window.CAMPO_API_URL = "http://localhost:8004";
