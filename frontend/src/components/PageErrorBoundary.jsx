import React from 'react'

export default class PageErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, errorMessage: '' }
  }

  static getDerivedStateFromError(error) {
    return {
      hasError: true,
      errorMessage: error instanceof Error ? error.message : String(error || 'Unknown UI error'),
    }
  }

  componentDidCatch(error, errorInfo) {
    // Keep the error visible in the browser console for debugging.
    console.error('PageErrorBoundary caught an error', error, errorInfo)
  }

  componentDidUpdate(prevProps) {
    if (prevProps.resetKey !== this.props.resetKey && this.state.hasError) {
      this.setState({ hasError: false, errorMessage: '' })
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="page-error-card">
          <p className="detail-eyebrow">UI Error</p>
          <h3>This section crashed, but the page is still available.</h3>
          <p>{this.state.errorMessage || 'Unknown UI error'}</p>
          <button type="button" onClick={() => this.setState({ hasError: false, errorMessage: '' })}>
            Try rendering again
          </button>
        </div>
      )
    }

    return this.props.children
  }
}
