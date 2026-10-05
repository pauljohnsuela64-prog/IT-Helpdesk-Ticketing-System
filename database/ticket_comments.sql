CREATE TABLE IF NOT EXISTS helpdesk.ticket_comments (
    comment_id INT AUTO_INCREMENT PRIMARY KEY,
    ticket_id INT NOT NULL,
    technician_id INT,
    comment_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ticket_comments_order (ticket_id, created_at, comment_id),
    CONSTRAINT fk_ticket_comments_ticket FOREIGN KEY (ticket_id)
        REFERENCES helpdesk.tickets (ticket_id) ON DELETE CASCADE,
    CONSTRAINT fk_ticket_comments_technician FOREIGN KEY (technician_id)
        REFERENCES helpdesk.technicians (technician_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
