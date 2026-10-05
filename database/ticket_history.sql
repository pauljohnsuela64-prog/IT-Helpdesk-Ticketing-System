CREATE TABLE IF NOT EXISTS helpdesk.ticket_history (
    history_id INT AUTO_INCREMENT PRIMARY KEY,
    ticket_id INT NOT NULL,
    action VARCHAR(100) NOT NULL,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ticket_history_order (ticket_id, created_at, history_id),
    CONSTRAINT fk_ticket_history_ticket FOREIGN KEY (ticket_id)
        REFERENCES helpdesk.tickets (ticket_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
