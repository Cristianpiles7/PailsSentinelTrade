# Registro de Proyectos PST (Pails Sentinel Trade)

Este documento centraliza el control de versiones y los identificadores de proyectos para mantener la trazabilidad de los cambios en el sistema.

## Catálogo de Proyectos

| Project ID | Nombre del Proyecto | Rama Git | Estado | Descripción |
| :--- | :--- | :--- | :--- | :--- |
| **PST-001** | Mean Reversion Optimization | `main` | ✅ Finalizado | Mejora de TP dinámico, agresivo y lógica de cortos. |
| **PST-002** | Dashboard Zoom Persistence | `main` | ✅ Finalizado | Fix del bug de refresco de gráficos en el dashboard. |
| **PST-003** | API-First Architecture | `feature/api-evolution` | 🏗️ Fase 2 | Migración a FastAPI y creación de Dashboard React. |

## Normas de Nomenclatura
- **Ramas:** `feature/[nombre-descriptivo]` o `fix/[bug-descriptivo]`.
- **Commits:** Incluir el Project ID (ej: `[PST-003] Setup FastAPI base`).
- **Versiones:** Se utilizará un sistema interno de IDs en lugar de versiones públicas (V1, V2...) para mayor flexibilidad.
