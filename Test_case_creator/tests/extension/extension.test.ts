import * as assert from 'assert';
import * as vscode from 'vscode';

suite('Extension Test Suite', () => {
  vscode.window.showInformationMessage('Start all extension tests.');

  test('Commands registered successfully', async () => {
    const commands = await vscode.commands.getCommands(true);
    
    // Sequence 6 commands
    assert.ok(commands.includes('test_case_creator.generateAll'), 'test_case_creator.generateAll should be registered');
    assert.ok(commands.includes('test_case_creator.customize'), 'test_case_creator.customize should be registered');
    assert.ok(commands.includes('test_case_creator.autoFixGaps'), 'test_case_creator.autoFixGaps should be registered');
    assert.ok(commands.includes('test_case_creator.exportAnyway'), 'test_case_creator.exportAnyway should be registered');

    // Compatibility commands
    assert.ok(commands.includes('testCaseCreator.generate'), 'testCaseCreator.generate should be registered');
    assert.ok(commands.includes('testCaseCreator.status'), 'testCaseCreator.status should be registered');
    assert.ok(commands.includes('testCaseCreator.autoFixGaps'), 'testCaseCreator.autoFixGaps should be registered');
    assert.ok(commands.includes('testCaseCreator.exportBestEffort'), 'testCaseCreator.exportBestEffort should be registered');
  });
});
