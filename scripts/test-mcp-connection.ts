/**
 * Tests the MCP server by connecting via Streamable HTTP and calling get-accounts.
 * Verifies that an account named "TESTING" exists in the response.
 */
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { CallToolResultSchema } from '@modelcontextprotocol/sdk/types.js';

const MCP_URL = process.env.MCP_URL || 'http://localhost:3001/mcp';

async function test(): Promise<void> {
  console.log(`Connecting to MCP server at ${MCP_URL}...`);

  const client = new Client({ name: 'e2e-test-client', version: '1.0.0' });
  const transport = new StreamableHTTPClientTransport(new URL(MCP_URL));

  await client.connect(transport);
  console.log('Connected to MCP server.');

  // Call get-accounts tool
  console.log('Calling get-accounts tool...');
  const result = await client.request(
    {
      method: 'tools/call',
      params: {
        name: 'get-accounts',
        arguments: {},
      },
    },
    CallToolResultSchema
  );

  // Parse the response
  const textContent = result.content.find((c) => c.type === 'text');
  if (!textContent || textContent.type !== 'text') {
    throw new Error('No text content in get-accounts response.');
  }

  console.log('get-accounts response:', textContent.text);

  const accounts: Array<{ name: string }> = JSON.parse(textContent.text);
  const testingAccount = accounts.find((a) => a.name === 'TESTING');

  if (!testingAccount) {
    throw new Error(
      `TESTING account not found. Accounts: ${accounts.map((a) => a.name).join(', ')}`
    );
  }

  console.log('TESTING account found!');

  // Clean up
  await transport.close();
  console.log('Test passed.');
}

test().catch((err) => {
  console.error('Test failed:', err);
  process.exit(1);
});
