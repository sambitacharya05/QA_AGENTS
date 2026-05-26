const path = require('path');
const CopyWebpackPlugin = require('copy-webpack-plugin');

module.exports = {
  target: 'node',
  entry: {
    extension: './src/extension.ts'
  },
  output: {
    path: path.resolve(__dirname, 'out'),
    filename: '[name].js',
    libraryTarget: 'commonjs'
  },
  externals: {
    vscode: 'commonjs vscode'
  },
  resolve: {
    extensions: ['.ts', '.js']
  },
  module: {
    rules: [
      {
        test: /\.ts$/,
        exclude: /node_modules/,
        use: [
          {
            loader: 'ts-loader'
          }
        ]
      }
    ]
  },
  plugins: [
    new CopyWebpackPlugin({
      patterns: [
        { from: 'src/agents/*.md', to: 'agents/[name][ext]' }
      ]
    })
  ],
  devtool: 'source-map'
};
