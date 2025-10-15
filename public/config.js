/**
 * EventFlow Configuration
 * 
 * This file contains configuration settings for the EventFlow application.
 * Modify these settings to customize the behavior of your application.
 */

const EventFlowConfig = {
    /**
     * Payment Test Mode
     * 
     * When enabled, payment transactions are simulated with animations
     * instead of processing through Razorpay. Perfect for demos and development.
     * 
     * true  = Simulated payments with animation (no Razorpay needed)
     * false = Real Razorpay integration (requires credentials)
     */
    USE_PAYMENT_TEST_MODE: true,

    /**
     * API Base URL
     * 
     * The base URL for backend API calls.
     * Change this if your backend is hosted elsewhere.
     */
    API_BASE_URL: 'http://localhost:5000',

    /**
     * Payment Animation Settings
     * 
     * Customize the duration of payment animations (in milliseconds)
     */
    PAYMENT_ANIMATION: {
        processingDuration: 2000,  // Time to show "Processing Payment" (2 seconds)
        successDuration: 1500,     // Time to show success message (1.5 seconds)
    },

    /**
     * UI Theme
     * 
     * Primary colors for the application
     */
    THEME: {
        primaryColor: '#6b21a8',    // Purple
        successColor: '#16a34a',    // Green
        errorColor: '#ef4444',      // Red
    }
};

// Export for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
    module.exports = EventFlowConfig;
}
