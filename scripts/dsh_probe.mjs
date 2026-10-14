// Development-only interceptor: inspect the real model-facing request, then stop.
// No request text, credentials, or novel content are written to the audit report.
import fs from 'node:fs';
import crypto from 'node:crypto';
export const name = 'novel2wiki-request-audit';
export const inject = ['llm', 'tools'];
export function apply(ctx) {
  ctx.on('llm/stream', (options, next) => {
    if (options.tools) {
      const hash = value => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
      const schemas = options.tools;
      // Message IDs and producer metadata are local persistence fields, not prompt text.
      const messages = (options.messages ?? []).map(m => ({role: m.role, content: m.content}));
      fs.writeFileSync(process.env.NOVEL2WIKI_AUDIT_OUT, JSON.stringify({
        kind: 'intercepted-before-provider-stream', toolNames: schemas.map(t => t.name ?? t.function?.name).sort(),
        toolsHash: hash(schemas), toolSchemaChars: JSON.stringify(schemas).length,
        messageCount: messages.length, messageChars: JSON.stringify(messages).length,
        messagesHash: hash(messages), modelApiCalled: false
      }, null, 2));
    }
    throw new Error('NOVEL2WIKI_AUDIT_COMPLETE_NO_MODEL_CALL');
  });
}
