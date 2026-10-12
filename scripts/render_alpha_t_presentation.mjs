// Internal/shareholder T renderer. Release approval remains a separate workflow.
process.env.T_PRESENTATION_MODE='internal';
await import('./render_alpha_t_private_presentation.mjs');
